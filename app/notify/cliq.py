"""Post messages to a Zoho Cliq channel (channel message endpoint + webhook token)."""

import logging

import httpx

log = logging.getLogger(__name__)


class CliqClient:
    def __init__(self, message_url: str, webhook_token: str, http: httpx.Client | None = None):
        self.url = message_url.rstrip("/")
        self.token = webhook_token
        self.http = http or httpx.Client(timeout=20)

    def post(self, text: str) -> bool:
        """Never raises: a failed notification must not break a verification."""
        try:
            r = self.http.post(self.url, params={"zapikey": self.token},
                               json={"text": text[:4000]})
            if r.status_code >= 300:
                log.warning("Cliq message failed: HTTP %s %s", r.status_code, r.text[:300])
                return False
            return True
        except httpx.HTTPError as exc:
            log.warning("Cliq message failed: %s", exc)
            return False
