"""Append rows to a Google Sheet with a service account (no person logs in).

Setup: Google Cloud > IAM > Service Accounts > create, Keys > JSON; share the spreadsheet
with the service account's email as Editor. See docs/notifications.md.
"""

import json
import logging
import os
import time

import httpx
import jwt

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def _read_key(path: str) -> str:
    """The key file as configured, else the same file name in ./secrets (scripts on the PC) or
    /run/secrets (inside Docker), so one setting works in both places."""
    name = os.path.basename(path)
    for candidate in (path, os.path.join("secrets", name), os.path.join("/run/secrets", name)):
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as f:
                return f.read()
    raise FileNotFoundError(f"Google service-account key not found: {path}")


class SheetsClient:
    def __init__(self, sheet_id: str, service_account: dict, http: httpx.Client | None = None):
        self.sheet_id = sheet_id
        self.sa = service_account
        self.http = http or httpx.Client(timeout=30)
        self._token: tuple[str, float] | None = None
        self._tabs: set[str] | None = None

    @classmethod
    def from_settings(cls, settings, http: httpx.Client | None = None) -> "SheetsClient | None":
        if not settings.google_sheet_id:
            return None
        raw = settings.google_service_account_json
        if not raw and settings.google_service_account_file:
            raw = _read_key(settings.google_service_account_file)
        if not raw:
            return None
        return cls(settings.google_sheet_id, json.loads(raw), http)

    # ---- auth: signed JWT -> access token (cached until 1 min before expiry) ----
    def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        now = int(time.time())
        assertion = jwt.encode({"iss": self.sa["client_email"], "scope": SCOPE,
                                "aud": self.sa.get("token_uri", TOKEN_URL),
                                "iat": now, "exp": now + 3600},
                               self.sa["private_key"], algorithm="RS256")
        r = self.http.post(self.sa.get("token_uri", TOKEN_URL), data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion})
        r.raise_for_status()
        body = r.json()
        self._token = (body["access_token"], now + int(body.get("expires_in", 3600)))
        return self._token[0]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._access_token()}"}

    # ---- tabs with header rows, created when missing ----
    def ensure_tabs(self, tabs: dict[str, list[str]]) -> None:
        if self._tabs is None:
            r = self.http.get(f"{SHEETS}/{self.sheet_id}", headers=self._headers(),
                              params={"fields": "sheets.properties.title"})
            r.raise_for_status()
            self._tabs = {s["properties"]["title"] for s in r.json().get("sheets", [])}
        missing = [t for t in tabs if t not in self._tabs]
        if not missing:
            return
        r = self.http.post(f"{SHEETS}/{self.sheet_id}:batchUpdate", headers=self._headers(),
                           json={"requests": [{"addSheet": {"properties": {"title": t}}}
                                              for t in missing]})
        r.raise_for_status()
        for t in missing:
            r = self.http.put(f"{SHEETS}/{self.sheet_id}/values/'{t}'!A1",
                              headers=self._headers(), params={"valueInputOption": "RAW"},
                              json={"values": [tabs[t]]})
            r.raise_for_status()
            self._tabs.add(t)

    def append(self, tab: str, rows: list[list]) -> bool:
        """Never raises: logging must not break a verification."""
        if not rows:
            return True
        try:
            r = self.http.post(
                f"{SHEETS}/{self.sheet_id}/values/'{tab}'!A1:append", headers=self._headers(),
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                json={"values": [["" if v is None else v for v in row] for row in rows]})
            if r.status_code >= 300:
                log.warning("Sheet append to %s failed: HTTP %s %s", tab, r.status_code,
                            r.text[:300])
                return False
            return True
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("Sheet append to %s failed: %s", tab, exc)
            return False
