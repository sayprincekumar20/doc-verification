import json
import shutil

import pytest

from app.tools.evaluate_reading import main
from tests import synthetic_docs as sd


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")
def test_evaluate_reports_classification_and_recall(tmp_path, capsys):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "dti.pdf").write_bytes(sd.digital_pdf())
    gold = {"dti.pdf": {"document_type": "DTI_BN_CERT", "fields": {
        "bn_number": "1234567", "owner_name": "JUAN DELA CRUZ", "valid_to": "11 May 2026",
        "not_on_page": "SOMETHING ELSE"}},
        "missing.jpg": {"document_type": "BIR_2303", "fields": {}}}
    (tmp_path / "gold.json").write_text(json.dumps(gold), encoding="utf-8")
    out = tmp_path / "report.json"
    assert main(["--gold", str(tmp_path / "gold.json"), "--files", str(tmp_path / "docs"),
                 "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["classification_accuracy"] == 1.0
    assert (report["fields_found"], report["fields_total"]) == (3, 4)
    assert "ERR   missing.jpg" in capsys.readouterr().out
