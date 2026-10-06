"""Tiny .env reader/writer for the setup scripts (keeps comments and other lines intact)."""

import os
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        # utf-8-sig: tolerate the byte-order mark some Windows editors add
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    # The file is the source of truth for the setup scripts (they write to it);
    # environment variables only fill keys the file doesn't define.
    for key, value in os.environ.items():
        if key.startswith(("ZOHO_", "API_KEY")) and key not in values:
            values[key] = value
    return values


def set_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)  # only the owner can read secrets
    except OSError:
        pass


def mask(secret: str) -> str:
    return secret[:9] + "…" + secret[-4:] if len(secret) > 16 else "***"
