import json
import shutil

import pytest

from app.tools import try_extraction
from tests import synthetic_docs as sd
from tests.fake_vision import FakeVision

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None,
                                reason="Tesseract not installed")


def test_try_extraction_folder_with_gold_template(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "IMG_0001.pdf").write_bytes(sd.digital_pdf())
    (folder / "notes.txt").write_text("ignored", encoding="utf-8")
    fake = FakeVision({"business_name": "EXAMPLE MARKET", "owner_name": "JUAN DELA CRUZ",
                       "bn_number": "1234567", "valid_from": "11 May 2021",
                       "valid_to": "11 May 2026"}, "DTI_BN_CERT")
    monkeypatch.setattr(try_extraction, "build_provider", lambda *a, **k: fake)
    gold = tmp_path / "draft.json"
    rc = try_extraction.main([str(folder), "--env", str(tmp_path / "none.env"),
                              "--today", "2026-10-06", "--gold-template", str(gold)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "classified: DTI_BN_CERT" in out and "EXPIRED" in out
    assert "bn_number" in out and "[EXACT 0.95]" in out
    saved = json.loads((folder / "extraction_results.json").read_text(encoding="utf-8"))
    assert saved[0]["validity"] == "EXPIRED"
    draft = json.loads(gold.read_text(encoding="utf-8"))
    assert draft["IMG_0001.pdf"]["_CHECK_ME"] is True
    assert draft["IMG_0001.pdf"]["fields"]["bn_number"] == "1234567"


def test_try_extraction_without_provider_reads_and_classifies(tmp_path, capsys):
    (tmp_path / "doc.pdf").write_bytes(sd.digital_pdf())
    (tmp_path / ".env").write_text("EXTRACTION_PROVIDER=none\n", encoding="utf-8")
    rc = try_extraction.main([str(tmp_path / "doc.pdf"), "--env", str(tmp_path / ".env")])
    out = capsys.readouterr().out
    assert rc == 0 and "classified: DTI_BN_CERT" in out and "reading + classification only" in out


def test_unreadable_page_is_reported_not_as_missing_provider(tmp_path, monkeypatch, capsys):
    from PIL import Image
    (tmp_path / "blank.png").write_bytes(sd.to_bytes(Image.new("RGB", (1200, 1600), "white")))
    monkeypatch.setattr(try_extraction, "build_provider",
                        lambda *a, **k: FakeVision({}, "OTHER"))
    try_extraction.main([str(tmp_path), "--env", str(tmp_path / "none.env")])
    out = capsys.readouterr().out
    assert "UNREADABLE: no readable page, not sent to the AI" in out
    assert "no AI provider configured" not in out
