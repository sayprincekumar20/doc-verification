import json

import httpx

from app.tools import check_zoho_setup
from app.zoho import review_module as spec

TYPE_FOR = {"autonumber": "autonumber", "lookup": "lookup", "userlookup": "userlookup",
            "picklist": "picklist", "text": "text", "textarea": "textarea",
            "text_or_textarea": "textarea", "integer": "integer", "decimal": "double",
            "datetime": "datetime", "boolean": "boolean", "subform": "subform"}


def zoho_field(fd: spec.FieldDef) -> dict:
    f = {"field_label": fd.label, "api_name": fd.label.replace(" ", "_"),
         "data_type": TYPE_FOR[fd.kind]}
    if fd.picklist:
        f["pick_list_values"] = [{"actual_value": v, "display_value": v} for v in fd.picklist]
    if fd.lookup_module:
        f["lookup"] = {"module": {"api_name": fd.lookup_module}}
    if fd.kind == "subform":
        f["associated_module"] = {"module": "Proposed_Changes"}
    return f


def fake_zoho(module_fields, subform_fields):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth/v2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if path.endswith("/settings/modules"):
            return httpx.Response(200, json={"modules": [
                {"api_name": "Accounts", "plural_label": "Accounts"},
                {"api_name": "Document_Verifications", "plural_label": "Document Verifications",
                 "singular_label": "Document Verification"}]})
        if path.endswith("/settings/fields"):
            module = request.url.params["module"]
            return httpx.Response(200, json={"fields": module_fields if module ==
                                             "Document_Verifications" else subform_fields})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def run(tmp_path, monkeypatch, module_fields, subform_fields, capsys):
    from app.tools import zoho_auth
    env = tmp_path / ".env.sandbox"
    env.write_text("ZOHO_CLIENT_ID=a\nZOHO_CLIENT_SECRET=b\nZOHO_REFRESH_TOKEN=c\n"
                   "ZOHO_API_DOMAIN=https://sandbox.zohoapis.com\n", encoding="utf-8")
    http = fake_zoho(module_fields, subform_fields)
    real = zoho_auth.build_client
    monkeypatch.setattr(check_zoho_setup, "build_client", lambda s: real(s, http))
    out = tmp_path / "names.json"
    rc = check_zoho_setup.main(["--env", str(env), "--out", str(out)])
    return rc, capsys.readouterr().out, out


def test_correct_setup_passes_and_saves_api_names(tmp_path, monkeypatch, capsys):
    rc, out, names = run(tmp_path, monkeypatch, [zoho_field(f) for f in spec.MODULE_FIELDS],
                         [zoho_field(f) for f in spec.SUBFORM_FIELDS], capsys)
    assert rc == 0 and "All checks passed." in out
    assert "sandbox.zohoapis.com" in out
    saved = json.loads(names.read_text(encoding="utf-8"))
    assert saved["module"] == "Document_Verifications"
    assert saved["fields"]["Review Status"] == "Review_Status"
    assert saved["subform"]["module"] == "Proposed_Changes"
    assert saved["subform_fields"]["Decision"] == "Decision"


def test_mistakes_are_reported(tmp_path, monkeypatch, capsys):
    module_fields = [zoho_field(f) for f in spec.MODULE_FIELDS if f.label != "Alerts"]
    for f in module_fields:
        if f["field_label"] == "Lowest Confidence":
            f["data_type"] = "text"
    sub = [zoho_field(f) for f in spec.SUBFORM_FIELDS]
    for f in sub:
        if f["field_label"] == "Action":
            f["pick_list_values"] = [v for v in f["pick_list_values"]
                                     if v["actual_value"] != "REVIEW_CONFLICT"]
    rc, out, _ = run(tmp_path, monkeypatch, module_fields, sub, capsys)
    assert rc == 1
    assert "MISSING field 'Alerts'" in out
    assert "WRONG TYPE 'Lowest Confidence'" in out
    assert "MISSING picklist value 'REVIEW_CONFLICT' in Proposed_Changes.Action" in out
    assert "3 problem(s) to fix in Zoho." in out
