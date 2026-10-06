"""Minimal Zoho CRM REST client with retries, token refresh and pagination."""

import logging
import random
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from app.config import Settings
from app.zoho.auth import ZohoTokenProvider
from app.zoho.errors import (
    FileTooLargeError,
    ZohoAPIError,
    ZohoNotFoundError,
    ZohoTransientError,
)

log = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}
# Field lists match the requests verified against your org (see docs/zoho-api.md).
ATTACHMENT_FIELDS = (
    "id,File_Name,File_Size,Created_Time,Modified_Time,Owner,Created_By,Modified_By,"
    "Parent_Id,Attachment_Type"
)
NOTE_FIELDS = (
    "id,Parent_Id,Owner,Created_By,Modified_By,Created_Time,Modified_Time,Note_Title,"
    "Note_Content,$attachments"
)


class ZohoClient:
    def __init__(
        self,
        settings: Settings,
        tokens: ZohoTokenProvider,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._s = settings
        self._tokens = tokens
        self._http = http or httpx.Client(timeout=settings.zoho_timeout_seconds)
        self._sleep = sleep
        self._base = f"{settings.zoho_api_domain}/crm/{settings.zoho_api_version}"

    # ---------- low level ----------

    def _backoff(self, attempt: int, resp: httpx.Response | None) -> float:
        if resp is not None and resp.headers.get("Retry-After", "").isdigit():
            return min(float(resp.headers["Retry-After"]), 120.0)
        return min(2**attempt + random.uniform(0, 1), 60.0)

    def _request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        url = f"{self._base}{path}"
        refreshed = False
        last_error = ""
        for attempt in range(self._s.zoho_max_retries + 1):
            headers = {"Authorization": f"Zoho-oauthtoken {self._tokens.get_token(refreshed)}"}
            try:
                resp = self._http.request(method, url, params=params, headers=headers)
            except httpx.TransportError as exc:
                last_error = f"network error: {exc}"
                self._sleep(self._backoff(attempt, None))
                continue

            if resp.status_code == 401 and not refreshed:
                refreshed = True  # token expired/invalid: refresh once and retry
                continue
            if resp.status_code in _RETRY_STATUS:
                last_error = f"HTTP {resp.status_code}"
                log.warning("Zoho %s %s -> %s, retrying", method, path, resp.status_code)
                self._sleep(self._backoff(attempt, resp))
                continue
            if resp.status_code >= 400:
                raise ZohoAPIError(resp.status_code, resp.text)
            return resp
        raise ZohoTransientError(f"{method} {path} failed after retries ({last_error})")

    def _get_json(self, path: str, params: dict | None = None) -> dict[str, Any]:
        resp = self._request("GET", path, params)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def _paginate(self, path: str, params: dict) -> Iterator[dict[str, Any]]:
        query: dict[str, Any] = {**params, "per_page": 200, "page": 1}
        while True:
            body = self._get_json(path, query)
            yield from body.get("data", [])
            info = body.get("info") or {}
            if not info.get("more_records"):
                return
            if info.get("next_page_token"):
                query.pop("page", None)
                query["page_token"] = info["next_page_token"]
            else:
                query["page"] += 1

    def _download(self, path: str, params: dict | None, max_bytes: int) -> bytes:
        resp = self._request("GET", path, params)
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise FileTooLargeError(int(declared), max_bytes)
        if len(resp.content) > max_bytes:
            raise FileTooLargeError(len(resp.content), max_bytes)
        return resp.content

    # ---------- records ----------

    def get_record(self, module: str, record_id: str) -> dict[str, Any]:
        body = self._get_json(f"/{module}/{record_id}")
        data = body.get("data") or []
        if not data:
            raise ZohoNotFoundError(f"{module} {record_id} not found")
        return data[0]

    # ---------- attachments / notes / files ----------

    def list_attachments(self, module: str, record_id: str) -> list[dict[str, Any]]:
        return list(self._paginate(f"/{module}/{record_id}/Attachments",
                                   {"fields": ATTACHMENT_FIELDS}))

    def list_notes(self, module: str, record_id: str) -> list[dict[str, Any]]:
        return list(self._paginate(f"/{module}/{record_id}/Notes", {"fields": NOTE_FIELDS}))

    def download_attachment(
        self, module: str, record_id: str, attachment_id: str, max_bytes: int
    ) -> bytes:
        return self._download(f"/{module}/{record_id}/Attachments/{attachment_id}", None, max_bytes)

    def download_file(self, file_id: str, max_bytes: int) -> bytes:
        """Download a file stored in a fileupload field (uses its encrypted File_Id__s)."""
        return self._download("/files", {"id": file_id}, max_bytes)
