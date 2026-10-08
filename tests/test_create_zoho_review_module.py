"""create_zoho_review_module.py against a stateful fake Zoho sandbox, then the checker."""

import json

import httpx
import pytest

from app.tools import check_zoho_setup, create_zoho_review_module, zoho_auth


class FakeSandbox:
    def __init__(self):
        self.modules = [{"api_name": "Accounts", "id": "111", "plural_label": "Accounts"}]
        self.fields: dict[str, list[dict]] = {}
        self.posts: list[tuple[str, dict]] = []
        self.fail_label: str | None = None

    def add_subform(self):
        self.fields["Document_Verifications"].append({
            "field_label": "Proposed Changes", "api_name": "Proposed_Changes",
            "data_type": "subform", "associated_module": {"module": "Proposed_Changes"}})
        self.fields["Proposed_Changes"] = [{"field_label": "Zoho Field", "api_name": "Zoho_Field",
                                            "data_type": "text"}]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path.endswith("/oauth/v2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if path.endswith("/settings/profiles"):
            return httpx.Response(200, json={"profiles": [{"id": "p1", "name": "Administrator"},
                                                          {"id": "p2", "name": "Standard"}]})
        if path.endswith("/settings/modules") and method == "GET":
            return httpx.Response(200, json={"modules": self.modules})
        if path.endswith("/settings/modules") and method == "POST":
            body = json.loads(request.content)
            self.posts.append(("module", body))
            m = body["modules"][0]
            self.modules.append({"api_name": "Document_Verifications", "id": "999",
                                 "plural_label": m["plural_label"],
                                 "singular_label": m["singular_label"]})
            self.fields["Document_Verifications"] = [{
                "field_label": m["display_field"]["field_label"], "api_name": "Name",
                "data_type": m["display_field"]["data_type"]}]
            return httpx.Response(201, json={"modules": [{"code": "SUCCESS",
                                                          "details": {"id": "999"}}]})
        if path.endswith("/settings/fields"):
            module = request.url.params["module"]
            if method == "GET":
                return httpx.Response(200, json={"fields": self.fields.get(module, [])})
            body = json.loads(request.content)
            assert len(body["fields"]) <= 5, "Zoho allows max 5 fields per call"
            self.posts.append((module, body))
            results = []
            for f in body["fields"]:
                if f["field_label"] == self.fail_label:
                    results.append({"code": "INVALID_DATA", "message": "invalid data",
                                    "details": {"api_name": f["field_label"]}})
                    continue
                stored = {"field_label": f["field_label"],
                          "api_name": f["field_label"].replace(" ", "_"),
                          "data_type": f["data_type"]}
                if "pick_list_values" in f:
                    stored["pick_list_values"] = f["pick_list_values"]
                if f["data_type"] == "lookup":
                    stored["lookup"] = {"module": {"api_name": f["lookup"]["module"]["api_name"]}}
                self.fields[module].append(stored)
                results.append({"code": "SUCCESS", "details": {"id": "f"}})
            status = 400 if any(r["code"] != "SUCCESS" for r in results) else 201
            return httpx.Response(status, json={"fields": results})
        return httpx.Response(404)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    fake = FakeSandbox()
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    real = zoho_auth.build_client
    monkeypatch.setattr(create_zoho_review_module, "build_client", lambda s: real(s, http))
    monkeypatch.setattr(check_zoho_setup, "build_client", lambda s: real(s, http))
    env = tmp_path / ".env.sandbox"
    env.write_text("ZOHO_CLIENT_ID=a\nZOHO_CLIENT_SECRET=b\nZOHO_REFRESH_TOKEN=c\n"
                   "ZOHO_API_DOMAIN=https://sandbox.zohoapis.com\n", encoding="utf-8")
    fake.env = env
    fake.tmp = tmp_path
    return fake


def run(sandbox, *extra):
    return create_zoho_review_module.main(["--env", str(sandbox.env), *extra])


def test_refuses_production(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("ZOHO_CLIENT_ID=a\nZOHO_CLIENT_SECRET=b\nZOHO_REFRESH_TOKEN=c\n"
                   "ZOHO_API_DOMAIN=https://www.zohoapis.com\n", encoding="utf-8")
    assert create_zoho_review_module.main(["--env", str(env), "--apply"]) == 2
    assert "is not a sandbox" in capsys.readouterr().out


def test_dry_run_changes_nothing(sandbox, capsys):
    assert run(sandbox) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "would create  Review Status (picklist)" in out
    assert sandbox.posts == []


def test_full_setup_then_checker_passes(sandbox, capsys):
    assert run(sandbox, "--apply") == 0
    out = capsys.readouterr().out
    assert "module created" in out and "Subform 'Proposed Changes' not found" in out
    module_body = sandbox.posts[0][1]["modules"][0]
    assert module_body["profiles"] == [{"id": "p1"}]
    assert module_body["display_field"]["auto_number"] == {"prefix": "DV", "start_number": 1}
    field_posts = [b for m, b in sandbox.posts if m == "Document_Verifications"]
    assert len(field_posts) == 4  # 16 fields in batches of 5
    lookup = next(f for b in field_posts for f in b["fields"] if f["field_label"] == "Account")
    assert lookup["lookup"]["module"] == {"api_name": "Accounts", "id": "111"}

    sandbox.add_subform()  # the one manual step
    assert run(sandbox, "--apply") == 0
    out = capsys.readouterr().out
    assert "2. Module fields: 0 missing" in out
    assert "10 column(s) missing" in out
    assert len([1 for m, _ in sandbox.posts if m == "Proposed_Changes"]) == 2

    assert run(sandbox, "--apply") == 0  # idempotent
    assert "0 column(s) missing" in capsys.readouterr().out

    names = sandbox.tmp / "names.json"
    assert check_zoho_setup.main(["--env", str(sandbox.env), "--out", str(names)]) == 0
    assert "All checks passed." in capsys.readouterr().out


def test_field_error_is_reported(sandbox, capsys):
    sandbox.fail_label = "Lowest Confidence"
    assert run(sandbox, "--apply") == 1
    out = capsys.readouterr().out
    assert "FAIL  Lowest Confidence: INVALID_DATA" in out and "1 problem(s)" in out
