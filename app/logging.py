"""JSON logging with masking of sensitive values (TINs, ID numbers, OAuth tokens)."""

import json
import logging
import re
import sys
from datetime import UTC, datetime

# Philippine TIN: 9 digits + optional 3-5 digit branch code, with or without separators.
_TIN_RE = re.compile(r"\b\d{3}[- ]?\d{3}[- ]?\d{3}(?:[- ]?\d{3,5})?\b")
_TOKEN_RE = re.compile(r"(Zoho-oauthtoken\s+)\S+", re.IGNORECASE)
_SECRET_KV_RE = re.compile(
    r"((?:access_token|refresh_token|client_secret|api_key)[\"']?\s*[:=]\s*[\"']?)[^\"'\s,&}]+",
    re.IGNORECASE,
)


def mask_sensitive(text: str) -> str:
    text = _TOKEN_RE.sub(r"\1***", text)
    text = _SECRET_KV_RE.sub(r"\1***", text)
    return _TIN_RE.sub(lambda m: "***" + re.sub(r"\D", "", m.group(0))[-3:], text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": mask_sensitive(record.getMessage()),
        }
        for key in ("job_id", "account_id", "document_id", "event"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = mask_sensitive(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # httpx logs full URLs at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
