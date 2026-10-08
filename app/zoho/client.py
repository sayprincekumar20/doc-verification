"""Minimal Zoho CRM REST client with retries, token refresh and pagination."""

import json
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


def _error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    return str(body.get("code", "")) if isinstance(body, dict) else ""


def _is_token_problem(resp: httpx.Response) -> bool:
    """401 because the token expired/is invalid (refresh helps), not because a scope is missing
    (refreshing would waste one of the 10-per-10-minutes token refreshes)."""
    return _error_code(resp) != "OAUTH_SCOPE_MISMATCH"


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

    def _request(self, method: str, path: str, params: dict | None = None,
                 json: dict | None = None, files: dict | None = None) -> httpx.Response:
        url = f"{self._base}{path}"
        refreshed = False
        last_error = ""
        for attempt in range(self._s.zoho_max_retries + 1):
            headers = {"Authorization": f"Zoho-oauthtoken {self._tokens.get_token(refreshed)}"}
            try:
                resp = self._http.request(method, url, params=params, json=json,
                                          files=files, headers=headers)
            except httpx.TransportError as exc:
                last_error = f"network error: {exc}"
                self._sleep(self._backoff(attempt, None))
                continue

            if resp.status_code == 401 and not refreshed and _is_token_problem(resp):
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

    def download_field_attachment(
        self, module: str, record_id: str, attachment_id: str, max_bytes: int
    ) -> bytes:
        """Download a file from a fileupload/imageupload field.

        GET /{module}/{record_id}/actions/download_fields_attachment?fields_attachment_id={id}
        Needs only the module READ scope. `attachment_id` is the field value's `attachment_Id`.
        """
        return self._download(
            f"/{module}/{record_id}/actions/download_fields_attachment",
            {"fields_attachment_id": attachment_id},
            max_bytes,
        )

    def download_file(self, file_id: str, max_bytes: int) -> bytes:
        """Fallback: fetch a Zoho File System file by encrypted id (needs ZohoCRM.Files.READ)."""
        return self._download("/files", {"id": file_id}, max_bytes)

    # ---------- updates ----------

    def update_record(self, module: str, record_id: str, values: dict[str, Any],
                      trigger: list[str] | None = None) -> dict[str, Any]:
        """PUT /{module} with one record. `trigger` = [] runs no workflows/approvals/blueprints
        (so our own update can't start another verification). Returns Zoho's per-record result;
        raises ZohoAPIError if Zoho rejects the record."""
        body: dict[str, Any] = {"data": [{"id": record_id, **values}]}
        if trigger is not None:
            body["trigger"] = trigger
        resp = self._request("PUT", f"/{module}", json=body)
        result = (resp.json().get("data") or [{}])[0]
        if result.get("code") != "SUCCESS":
            raise ZohoAPIError(resp.status_code, str(result))
        return result

    def _create(self, path: str, record: dict[str, Any]) -> str:
        resp = self._request("POST", path, json={"data": [record]})
        result = (resp.json().get("data") or [{}])[0]
        if result.get("code") != "SUCCESS":
            raise ZohoAPIError(resp.status_code, str(result))
        return str((result.get("details") or {}).get("id", ""))

    def create_record(self, module: str, record: dict[str, Any]) -> str:
        """POST /{module}; returns the new record id (custom modules: scope
        ZohoCRM.modules.custom.CREATE)."""
        return self._create(f"/{module}", record)

    def upload_attachment(self, module: str, record_id: str, file_name: str, data: bytes,
                          content_type: str = "application/octet-stream") -> str:
        """POST /{module}/{id}/Attachments (multipart). Scope ZohoCRM.modules.attachments.CREATE."""
        resp = self._request("POST", f"/{module}/{record_id}/Attachments",
                             files={"file": (file_name, data, content_type)})
        result = (resp.json().get("data") or [{}])[0]
        if result.get("code") != "SUCCESS":
            raise ZohoAPIError(resp.status_code, str(result))
        return str((result.get("details") or {}).get("id", ""))

    def create_note(self, module: str, record_id: str, title: str, content: str) -> str:
        """Note on a record (shown in its Notes section). Scope ZohoCRM.modules.notes.CREATE."""
        return self._create(f"/{module}/{record_id}/Notes",
                            {"Note_Title": title[:120], "Note_Content": content})

    def create_task(self, subject: str, description: str, due_date: str, account_id: str,
                    owner_id: str | None = None) -> str:
        """Task linked to an Account, assigned to its owner. Scope ZohoCRM.modules.tasks.CREATE."""
        task: dict[str, Any] = {"Subject": subject[:250], "Description": description,
                                "Due_Date": due_date, "Status": "Not Started",
                                "Priority": "High", "$se_module": "Accounts",
                                "What_Id": {"id": account_id}}
        if owner_id:
            task["Owner"] = {"id": owner_id}
        return self._create("/Tasks", task)

    # ---------- setup checks ----------

    def get_current_user(self) -> dict[str, Any]:
        body = self._get_json("/users", {"type": "CurrentUser"})
        users = body.get("users") or []
        if not users:
            raise ZohoNotFoundError("Current user not returned")
        return users[0]

    def list_profiles(self) -> list[dict[str, Any]]:
        return self._get_json("/settings/profiles").get("profiles") or []

    def _post_settings(self, path: str, body: dict, key: str,
                       params: dict | None = None) -> list[dict[str, Any]]:
        """POST to a settings API; returns Zoho's per-item results (also on HTTP 400, so
        the caller can show which item failed and why)."""
        try:
            resp = self._request("POST", path, params=params, json=body)
            return resp.json().get(key) or []
        except ZohoAPIError as exc:
            try:
                parsed = json.loads(exc.body)
            except ValueError:
                raise exc from None
            items = parsed.get(key) if isinstance(parsed, dict) else None
            if items:
                return items
            raise

    def create_module(self, body: dict) -> list[dict[str, Any]]:
        """POST /settings/modules (scope ZohoCRM.settings.modules.CREATE)."""
        return self._post_settings("/settings/modules", body, "modules")

    def create_fields(self, module: str, fields: list[dict]) -> list[dict[str, Any]]:
        """POST /settings/fields?module= (max 5 fields; scope ZohoCRM.settings.fields.CREATE)."""
        return self._post_settings("/settings/fields", {"fields": fields}, "fields",
                                   {"module": module})

    def list_modules(self) -> list[dict[str, Any]]:
        return self._get_json("/settings/modules").get("modules") or []

    def get_fields(self, module: str) -> list[dict[str, Any]]:
        return self._get_json("/settings/fields", {"module": module}).get("fields") or []

    def count_fields(self, module: str) -> int:
        return len(self._get_json("/settings/fields", {"module": module}).get("fields") or [])

    def list_records(self, module: str, fields: str, per_page: int = 1) -> list[dict[str, Any]]:
        return self._get_json(f"/{module}", {"fields": fields, "per_page": per_page}).get(
            "data"
        ) or []
