"""Zoho OAuth setup and checks.

    python scripts/zoho_auth.py scopes                 # scopes to paste in the API Console
    python scripts/zoho_auth.py exchange-code          # grant code -> refresh token in .env
    python scripts/zoho_auth.py test [--account-id ID] # verify credentials and scopes
    python scripts/zoho_auth.py revoke                 # revoke the refresh token in .env
"""

import argparse
import getpass
import sys
from pathlib import Path

import httpx

from app.config import Settings
from app.tools.envfile import mask, read_env, set_env_value
from app.zoho.auth import (
    MemoryTokenCache,
    ZohoTokenProvider,
    exchange_grant_code,
    revoke_refresh_token,
)
from app.zoho.client import ZohoClient
from app.zoho.errors import ZohoAPIError, ZohoError

# Read scopes for collection. UPDATE / notes.CREATE / tasks.CREATE are needed only for
# automatic actions (AUTO_APPLY_MODE=on): field updates, activation, alert notes and tasks.
SCOPES = [
    "ZohoCRM.modules.accounts.READ",
    "ZohoCRM.modules.accounts.UPDATE",
    "ZohoCRM.modules.notes.CREATE",
    "ZohoCRM.modules.tasks.CREATE",
    "ZohoCRM.modules.notes.READ",
    "ZohoCRM.modules.attachments.READ",
    "ZohoCRM.settings.fields.READ",
    "ZohoCRM.settings.modules.READ",
    "ZohoCRM.users.READ",
    "ZohoCRM.Files.READ",
]
# Extra scopes only for scripts/create_zoho_review_module.py (one-time setup, admin user).
SETUP_SCOPES = [
    "ZohoCRM.settings.modules.CREATE",
    "ZohoCRM.settings.fields.CREATE",
    "ZohoCRM.settings.profiles.READ",
]
DEFAULT_ACCOUNTS_URL = "https://accounts.zoho.com"
DEFAULT_API_DOMAIN = "https://www.zohoapis.com"


def build_settings(env: dict[str, str]) -> Settings:
    missing = [k for k in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN")
               if not env.get(k)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)}")
    return Settings(
        _env_file=None,
        api_key=env.get("API_KEY") or "not-used-by-scripts",
        zoho_client_id=env["ZOHO_CLIENT_ID"],
        zoho_client_secret=env["ZOHO_CLIENT_SECRET"],
        zoho_refresh_token=env["ZOHO_REFRESH_TOKEN"],
        zoho_accounts_url=env.get("ZOHO_ACCOUNTS_URL") or DEFAULT_ACCOUNTS_URL,
        zoho_api_domain=env.get("ZOHO_API_DOMAIN") or DEFAULT_API_DOMAIN,
        zoho_api_version=env.get("ZOHO_API_VERSION") or "v8",
        zoho_max_retries=2,
    )


def build_client(settings: Settings, http: httpx.Client | None = None) -> ZohoClient:
    tokens = ZohoTokenProvider(settings, MemoryTokenCache(), http=http)
    return ZohoClient(settings, tokens, http=http)


def cmd_scopes(args: argparse.Namespace) -> int:
    print(",".join(SCOPES + (SETUP_SCOPES if getattr(args, "setup", False) else [])))
    return 0


def cmd_exchange(args: argparse.Namespace, http: httpx.Client | None = None) -> int:
    env_path = Path(args.env)
    env = read_env(env_path)
    for key in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET"):
        if not env.get(key):
            raise SystemExit(f"Set {key} in {env_path} first (from the API Console Self Client).")
    code = args.code or getpass.getpass("Paste the grant code (input hidden): ")
    body = exchange_grant_code(env.get("ZOHO_ACCOUNTS_URL") or DEFAULT_ACCOUNTS_URL,
                               env["ZOHO_CLIENT_ID"], env["ZOHO_CLIENT_SECRET"], code, http=http)
    set_env_value(env_path, "ZOHO_REFRESH_TOKEN", body["refresh_token"])
    api_domain = body.get("api_domain")
    if api_domain and api_domain != (env.get("ZOHO_API_DOMAIN") or DEFAULT_API_DOMAIN):
        set_env_value(env_path, "ZOHO_API_DOMAIN", api_domain)
    print(f"OK  refresh token saved to {env_path} ({mask(body['refresh_token'])})")
    print(f"    api_domain: {api_domain}")
    print("    Next: python scripts/zoho_auth.py test")
    return 0


def _check(label: str, fn) -> bool:
    try:
        detail = fn()
        print(f"OK    {label}" + (f": {detail}" if detail else ""))
        return True
    except ZohoAPIError as exc:
        scope_missing = "OAUTH_SCOPE_MISMATCH" in exc.body
        reason = "missing scope" if scope_missing else f"HTTP {exc.status_code}"
        print(f"FAIL  {label}: {reason} - {exc.body[:200]}")
    except ZohoError as exc:
        print(f"FAIL  {label}: {exc}")
    return False


def cmd_test(args: argparse.Namespace, http: httpx.Client | None = None) -> int:
    settings = build_settings(read_env(Path(args.env)))
    client = build_client(settings, http)

    def token():
        client._tokens.get_token()  # noqa: SLF001 - setup check
        return f"access token issued ({settings.zoho_accounts_url})"

    def user():
        u = client.get_current_user()
        profile = (u.get("profile") or {}).get("name")
        return f"{u.get('full_name')} <{u.get('email')}>, profile {profile}"

    results = [
        _check("Token refresh", token),
        _check("ZohoCRM.users.READ (current user)", user),
        _check("ZohoCRM.settings.fields.READ (Accounts fields)",
               lambda: f"{client.count_fields('Accounts')} fields"),
        _check("ZohoCRM.modules.accounts.READ (list Accounts)",
               lambda: f"{len(client.list_records('Accounts', 'id'))} record(s) readable"),
    ]
    if args.account_id:
        aid = args.account_id
        results += [
            _check(f"Read Account {aid}",
                   lambda: client.get_record("Accounts", aid).get("Account_Name")),
            _check("ZohoCRM.modules.notes.READ (Notes)",
                   lambda: f"{len(client.list_notes('Accounts', aid))} note(s)"),
            _check("ZohoCRM.modules.attachments.READ (Attachments)",
                   lambda: f"{len(client.list_attachments('Accounts', aid))} attachment(s)"),
        ]
    else:
        print("INFO  add --account-id <id> to also check Notes and Attachments access")

    ok = all(results)
    print("\nAll checks passed." if ok else "\nSome checks failed. See docs/zoho-auth.md.")
    return 0 if ok else 1


def cmd_revoke(args: argparse.Namespace, http: httpx.Client | None = None) -> int:
    env_path = Path(args.env)
    env = read_env(env_path)
    token = env.get("ZOHO_REFRESH_TOKEN")
    if not token:
        raise SystemExit("No ZOHO_REFRESH_TOKEN in .env")
    revoke_refresh_token(env.get("ZOHO_ACCOUNTS_URL") or DEFAULT_ACCOUNTS_URL, token, http=http)
    set_env_value(env_path, "ZOHO_REFRESH_TOKEN", "")
    print(f"OK  refresh token {mask(token)} revoked and removed from {env_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zoho OAuth setup for the verification engine")
    parser.add_argument("--env", default=".env", help="path to the .env file (default: .env)")
    sub = parser.add_subparsers(dest="command", required=True)
    sc = sub.add_parser("scopes", help="print the scopes for the API Console")
    sc.add_argument("--setup", action="store_true",
                    help="include the scopes for creating the review module (one-time setup)")
    ex = sub.add_parser("exchange-code", help="exchange a grant code for a refresh token")
    ex.add_argument("--code", help="grant code (omit to be prompted, hidden)")
    te = sub.add_parser("test", help="verify credentials and scopes")
    te.add_argument("--account-id", help="also check Notes/Attachments for this Account")
    sub.add_parser("revoke", help="revoke the refresh token in .env")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    commands = {"scopes": cmd_scopes, "exchange-code": cmd_exchange, "test": cmd_test,
                "revoke": cmd_revoke}
    try:
        return commands[args.command](args)
    except ZohoError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
