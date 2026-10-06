"""Zoho OAuth: exchanges the long-lived refresh token for 1-hour access tokens.

Access tokens are cached (Redis when available) and refreshed under a lock, because Zoho limits
how often new access tokens can be generated from one refresh token.
"""

import logging
import threading
import time
from typing import Protocol

import httpx

from app.config import Settings
from app.zoho.errors import ZohoAuthError, ZohoTransientError

log = logging.getLogger(__name__)

_CACHE_KEY = "zoho:access_token"
_LOCK_KEY = "zoho:access_token:lock"
_SAFETY_MARGIN_SECONDS = 300


class TokenCache(Protocol):
    def get(self) -> str | None: ...
    def set(self, token: str, ttl_seconds: int) -> None: ...
    def lock(self): ...  # context manager


class MemoryTokenCache:
    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get(self) -> str | None:
        return self._token if self._token and time.time() < self._expires_at else None

    def set(self, token: str, ttl_seconds: int) -> None:
        self._token, self._expires_at = token, time.time() + ttl_seconds

    def lock(self):
        return self._lock


class RedisTokenCache:
    def __init__(self, redis_client) -> None:
        self._r = redis_client

    def get(self) -> str | None:
        value = self._r.get(_CACHE_KEY)
        return value.decode() if isinstance(value, bytes) else value

    def set(self, token: str, ttl_seconds: int) -> None:
        self._r.set(_CACHE_KEY, token, ex=max(ttl_seconds, 60))

    def lock(self):
        return self._r.lock(_LOCK_KEY, timeout=30, blocking_timeout=30)


class ZohoTokenProvider:
    def __init__(
        self,
        settings: Settings,
        cache: TokenCache | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self._s = settings
        self._cache = cache or MemoryTokenCache()
        self._http = http or httpx.Client(timeout=30)

    def get_token(self, force_refresh: bool = False) -> str:
        if not force_refresh:
            token = self._cache.get()
            if token:
                return token
        with self._cache.lock():
            if not force_refresh:
                token = self._cache.get()  # another worker may have refreshed meanwhile
                if token:
                    return token
            return self._refresh()

    def _refresh(self) -> str:
        url = f"{self._s.zoho_accounts_url}/oauth/v2/token"
        data = {
            "refresh_token": self._s.zoho_refresh_token.get_secret_value(),
            "client_id": self._s.zoho_client_id,
            "client_secret": self._s.zoho_client_secret.get_secret_value(),
            "grant_type": "refresh_token",
        }
        try:
            resp = self._http.post(url, data=data)
        except httpx.TransportError as exc:
            raise ZohoTransientError(f"Token endpoint unreachable: {exc}") from exc

        if resp.status_code >= 500 or resp.status_code == 429:
            raise ZohoTransientError(f"Token endpoint returned {resp.status_code}")
        body = resp.json() if resp.content else {}
        if resp.status_code != 200 or "access_token" not in body:
            # e.g. {"error": "invalid_code"} when the refresh token was revoked
            raise ZohoAuthError(f"Token refresh failed: {body.get('error', resp.status_code)}")

        ttl = int(body.get("expires_in", 3600)) - _SAFETY_MARGIN_SECONDS
        self._cache.set(body["access_token"], ttl)
        log.info("Zoho access token refreshed")
        return body["access_token"]
