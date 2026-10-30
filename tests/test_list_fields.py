from app.tools import list_fields


class Fake:
    def get_fields(self, module):
        return [{"api_name": "Credit_Terms", "field_label": "Payment Terms",
                 "data_type": "picklist", "pick_list_values": [
                     {"actual_value": "-None-"}, {"actual_value": "Cash On Delivery"}]},
                {"api_name": "Parent_Account", "field_label": "Parent Account",
                 "data_type": "lookup", "lookup": {"module": {"api_name": "Accounts"}}}]


def test_exports_definitions(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ZOHO_CLIENT_ID=x\nZOHO_CLIENT_SECRET=x\nZOHO_REFRESH_TOKEN=x\nAPI_KEY=x\n")
    monkeypatch.setattr(list_fields, "build_client", lambda s: Fake())
    assert list_fields.main(["--env", str(env), "--out", str(tmp_path / "f")]) == 0
    md = (tmp_path / "f.md").read_text()
    assert "| Payment Terms | Credit_Terms | picklist | Cash On Delivery |" in md
    assert "| Parent Account | Parent_Account | lookup | -> Accounts |" in md
