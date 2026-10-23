"""copy_documents: upload a client's files to a test Account, skipping names already there."""

import json

from app.tools import copy_documents


class FakeTarget:
    def __init__(self):
        self.uploads = []

    def get_record(self, module, record_id):
        return {"id": record_id, "Account_Name": "TEST HQ A"}

    def list_attachments(self, module, record_id):
        return [{"File_Name": "DTI.jpg"}]

    def upload_attachment(self, module, record_id, name, data, kind):
        self.uploads.append((record_id, name, len(data), kind))
        return "1"


def _folder(tmp_path):
    files = tmp_path / "src" / "files"
    files.mkdir(parents=True)
    (files / "attachment_1_DTI.jpg").write_bytes(b"dti")
    (files / "attachment_2_2303.jpg").write_bytes(b"bir-2303")
    (files / "note_3_permit.pdf").write_bytes(b"%PDF-1.4 permit")
    manifest = {"account_id": "590", "account_name": "WINNER", "files": [
        {"file_name": "DTI.jpg", "saved_as": "files/attachment_1_DTI.jpg", "status": "OK"},
        {"file_name": "2303.jpg", "saved_as": "files/attachment_2_2303.jpg", "status": "OK"},
        {"file_name": "permit.pdf", "saved_as": "files/note_3_permit.pdf", "status": "OK"},
        {"file_name": "bad.exe", "saved_as": "files/x", "status": "UNSUPPORTED"}]}
    (tmp_path / "src" / "manifest.json").write_text(json.dumps(manifest))
    env = tmp_path / ".env.sandbox"
    env.write_text("ZOHO_CLIENT_ID=x\nZOHO_CLIENT_SECRET=x\nZOHO_REFRESH_TOKEN=x\nAPI_KEY=x\n")
    return tmp_path / "src", env


def test_copies_new_files_with_original_names(tmp_path, monkeypatch):
    src, env = _folder(tmp_path)
    target = FakeTarget()
    monkeypatch.setattr(copy_documents, "build_client", lambda s: target)
    rc = copy_documents.main(["--from-folder", str(src), "--to-account", "477",
                              "--to-env", str(env)])
    assert rc == 0
    assert [(n, k) for _, n, _, k in target.uploads] == [("2303.jpg", "image/jpeg"),
                                                         ("permit.pdf", "application/pdf")]


def test_dry_run_uploads_nothing(tmp_path, monkeypatch):
    src, env = _folder(tmp_path)
    target = FakeTarget()
    monkeypatch.setattr(copy_documents, "build_client", lambda s: target)
    copy_documents.main(["--from-folder", str(src), "--to-account", "477",
                         "--to-env", str(env), "--dry-run"])
    assert target.uploads == []
