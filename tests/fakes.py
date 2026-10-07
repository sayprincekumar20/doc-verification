"""A fake Zoho CRM served through httpx.MockTransport.

`FakeZoho.from_real_responses()` replays representative Zoho responses from tests/fixtures/*.json.
"""

import copy
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
PDF = b"%PDF-1.4\n" + b"0" * 200
EXE = b"MZ" + b"\x00" * 200

ACCOUNT_ID = "1000000000000001"
FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeZoho:
    def __init__(self):
        self.account = {"id": ACCOUNT_ID, "Account_Name": "Example Market",
                "Inventory_Customer_Number": "EXAMPLE-001"}
        self.attachments: list[dict] = []        # {"id", "name", "data"} (simple tests)
        self.attachments_body: dict | None = None  # raw list response (real-payload tests)
        self.attachment_bytes: dict[str, bytes] = {}
        self.notes: list[dict] = []
        self.notes_body: dict | None = None
        self.files: dict[str, bytes] = {}
        self.fail_next: list[int] = []
        self.calls: list[str] = []
        self.params: list[dict] = []
        self.token_requests = 0
        self.field_attachments: dict[str, bytes] = {}
        self.scope_missing_paths: set[str] = set()  # paths answering 401 OAUTH_SCOPE_MISMATCH
        self.revoked: list[str] = []
        self.updates: list[dict] = []
        self.update_error: dict | None = None

    @classmethod
    def from_real_responses(cls) -> "FakeZoho":
        fake = cls()
        fake.account = copy.deepcopy(load("account")["data"][0])
        fake.notes_body = load("notes")
        fake.attachments_body = load("attachments")
        for i, item in enumerate(fake.attachments_body["data"]):
            fake.attachment_bytes[item["id"]] = JPEG + bytes([i])  # three different photos
        return fake

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        self.params.append(dict(request.url.params))
        if path.endswith("/oauth/v2/token/revoke"):
            self.revoked.append(parse_qs(request.content.decode()).get("token", [""])[0])
            return httpx.Response(200, json={"status": "success"})
        if path.endswith("/oauth/v2/token"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form.get("grant_type") == "authorization_code":
                if form.get("code") != "1000.goodcode":
                    return httpx.Response(200, json={"error": "invalid_code"})
                return httpx.Response(200, json={
                    "access_token": "1000.access", "refresh_token": "1000.refresh.abcdefghijkl",
                    "api_domain": "https://www.zohoapis.com", "token_type": "Bearer",
                    "expires_in": 3600})
            self.token_requests += 1
            return httpx.Response(200, json={"access_token": f"tok{self.token_requests}",
                                             "expires_in": 3600})
        if path in self.scope_missing_paths:
            return httpx.Response(401, json={"code": "OAUTH_SCOPE_MISMATCH",
                                             "message": "invalid oauth scope"})
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), json={"code": "ERR"})

        base = "/crm/v8"
        if request.method == "PUT" and path == f"{base}/Accounts":
            body = json.loads(request.content)
            self.updates.append(body)
            if self.update_error:
                return httpx.Response(400, json={"data": [self.update_error]})
            record = body["data"][0]
            self.account.update({k: v for k, v in record.items() if k != "id"})
            return httpx.Response(200, json={"data": [{"code": "SUCCESS", "status": "success",
                                                       "details": {"id": record["id"]}}]})
        if path == f"{base}/users":
            return httpx.Response(200, json={"users": [{
                "full_name": "Example Integration", "email": "integration@example.com",
                "profile": {"name": "Administrator"}, "role": {"name": "CEO"}}]})
        if path == f"{base}/settings/fields":
            return httpx.Response(200, json={"fields": [{"api_name": "Account_Name"},
                                                        {"api_name": "Customer_Status"}]})
        if path == f"{base}/Accounts":
            return httpx.Response(200, json={"data": [{"id": ACCOUNT_ID}],
                                             "info": {"more_records": True}})
        if path == f"{base}/Accounts/{ACCOUNT_ID}/actions/download_fields_attachment":
            att = request.url.params.get("fields_attachment_id")
            if att in self.field_attachments:
                return httpx.Response(200, content=self.field_attachments[att])
            return httpx.Response(400, json={"code": "INVALID_DATA"})
        if path == f"{base}/Accounts/{ACCOUNT_ID}":
            return httpx.Response(200, json={"data": [self.account]})
        if path == f"{base}/Accounts/{ACCOUNT_ID}/Attachments":
            if self.attachments_body is not None:
                return httpx.Response(200, json=self.attachments_body)
            if not self.attachments:
                return httpx.Response(204)
            return httpx.Response(200, json={"data": [
                {"id": a["id"], "File_Name": a["name"],
                 "Created_By": {"email": "aj@example.com"}} for a in self.attachments
            ], "info": {"more_records": False}})
        if path.startswith(f"{base}/Accounts/{ACCOUNT_ID}/Attachments/"):
            att_id = path.rsplit("/", 1)[1]
            if att_id in self.attachment_bytes:
                return httpx.Response(200, content=self.attachment_bytes[att_id])
            for a in self.attachments:
                if a["id"] == att_id:
                    return httpx.Response(200, content=a["data"])
            return httpx.Response(404)
        if path == f"{base}/Accounts/{ACCOUNT_ID}/Notes":
            if self.notes_body is not None:
                return httpx.Response(200, json=self.notes_body)
            if not self.notes:
                return httpx.Response(204)
            return httpx.Response(200, json={"data": self.notes, "info": {"more_records": False}})
        if path.startswith(f"{base}/Notes/"):
            att_id = path.rsplit("/", 1)[1]
            return httpx.Response(200, content=self.files[att_id])
        if path == f"{base}/files":
            return httpx.Response(200, content=self.files[request.url.params["id"]])
        return httpx.Response(404, content=json.dumps({"path": path}))

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))
