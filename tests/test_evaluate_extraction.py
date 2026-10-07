import shutil
from datetime import date

import pytest

from app.extraction.compare import DIFFERENT, MINOR_DIFF, SAME, compare
from app.tools.evaluate_extraction import evaluate
from tests import synthetic_docs as sd
from tests.fake_vision import FakeVision


def test_compare_rules():
    assert compare("name", "FLORES, MARICHELLE", "MARICHELLE FLORES") == SAME
    assert compare("name", "WINNER'S MEAT SHOP", "WINNER MEAT SHOP") == SAME
    assert compare("date", "11 May 2026", "2026-05-11") == SAME
    assert compare("tin", "601-088-612-00000", "601088612") == SAME
    assert compare("code", "2025 02655", "202502655") == SAME
    assert compare("code", "1234561", "1234567") == DIFFERENT
    assert compare("address", "PUROK 1 PUYPUY BAY LAGUNA", "PUROK 1 PUYPUY, BAY, LAGUNA.") == SAME
    assert compare("address", "PUROK 1 PUYPUY 4033 BAY LAGUNA",
                   "PUROK 1 PUYPUY BAY LAGUNA") == MINOR_DIFF
    assert compare("text", "x", None) is None


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")
def test_evaluate_counts_outcomes_and_silent_errors(tmp_path):
    (tmp_path / "dti.pdf").write_bytes(sd.digital_pdf())
    gold = {"dti.pdf": {"document_type": "DTI_BN_CERT", "fields": {
        "bn_number": "1234567", "owner_name": "JUAN DELA CRUZ", "valid_to": "11 May 2026",
        "territorial_scope": "CITY/MUNICIPALITY"}}}
    # model: one correct, one wrong digit, one missed, one correct name in other order
    fake = FakeVision({"bn_number": "1234561", "owner_name": "DELA CRUZ, JUAN",
                       "valid_to": "11 May 2026", "territorial_scope": None}, "DTI_BN_CERT")
    report = evaluate(gold, [tmp_path], fake, date(2025, 1, 1))
    assert report["outcomes"] == {"WRONG": 1, "CORRECT": 2, "MISSED": 1}
    assert report["wrong_but_looked_confirmed"] == 0       # the wrong digit was flagged
    assert report["wrong_by_grounding"] == {"CONFLICT": 1}


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")
def test_gold_value_can_list_acceptable_answers(tmp_path):
    (tmp_path / "dti.pdf").write_bytes(sd.digital_pdf())
    gold = {"dti.pdf": {"document_type": "DTI_BN_CERT", "fields": {
        "territorial_scope": ["CITY/MUNICIPALITY", "MUNICIPALITY"]}}}
    fake = FakeVision({"territorial_scope": "MUNICIPALITY"}, "DTI_BN_CERT")
    assert evaluate(gold, [tmp_path], fake, date(2025, 1, 1))["outcomes"] == {"CORRECT": 1}
