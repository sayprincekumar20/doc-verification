"""Phase 1C: normalization, grounding, validation and the extraction orchestration."""

from datetime import date

import cv2
import numpy as np
import pytest

from app.extraction.extract import PageInput, extract_document
from app.extraction.grounding import CONFLICT, EXACT, FUZZY, NOT_FOUND, ground
from app.extraction.normalize import business_key, name_key, normalize, parse_date
from app.extraction.providers import ExtractionError, ModelOutput
from tests.fake_vision import FakeVision

JPEG = cv2.imencode(".jpg", np.full((50, 50, 3), 255, np.uint8))[1].tobytes()
DTI_TEXT = """This certifies that EXAMPLE MARKET (CITY/MUNICIPALITY) This certificate issued to
JUAN DELA CRUZ is valid from 11 May 2021 to 11 May 2026 Business Name No. 1234567"""
DTI_VALUES = {"business_name": "EXAMPLE MARKET", "owner_name": "JUAN DELA CRUZ",
              "bn_number": "1234567", "valid_from": "11 May 2021", "valid_to": "11 May 2026",
              "territorial_scope": "CITY/MUNICIPALITY", "location": None}


@pytest.mark.parametrize("raw,iso", [
    ("11 May 2021", "2021-05-11"), ("August 25, 2021", "2021-08-25"),
    ("1/22/2025", "2025-01-22"), ("31/12/2025", "2025-12-31"),
    ("22nd day of January 2025", "2025-01-22"), ("Sept 3, 2024", "2024-09-03"),
])
def test_parse_ph_dates(raw, iso):
    assert parse_date(raw).isoformat() == iso


def test_normalize_kinds():
    assert normalize("tin", "601 088 612") == "601-088-612-00000"
    assert normalize("tin", "12345") is None
    assert normalize("money", "₱17,536.00") == "17536.00"
    assert name_key("FLORES, MARICHELLE") == name_key("MARICHELLE FLORES")
    assert business_key("WINNER'S MEAT SHOP") == business_key("WINNER MEAT SHOP")
    assert business_key("BISTRO AMERICANO CORP.") == business_key("Bistro Americano")


def test_grounding_states():
    assert ground("1234567", "code", DTI_TEXT).status == EXACT
    assert ground("2021-05-11", "date", DTI_TEXT).status == EXACT     # rendered as 11 May 2021
    assert ground("JUAN DELA CRUS", "name", DTI_TEXT).status == FUZZY  # OCR-style typo
    assert ground("1234561", "code", DTI_TEXT).status == CONFLICT      # one digit differs
    assert ground("11 May 2027", "date", DTI_TEXT).status == CONFLICT
    assert ground("PEDRO PENDUKO", "name", DTI_TEXT).status == NOT_FOUND
    assert ground("BR", "code", "XBRX").status == EXACT
    assert ground("Q9", "code", "").status == NOT_FOUND


def _pages(text=DTI_TEXT, quality="GOOD"):
    return [PageInput(JPEG, text, quality)]


def test_extract_dti_valid_and_grounded():
    r = extract_document(_pages(), "DTI_BN_CERT", FakeVision(DTI_VALUES, "DTI_BN_CERT"),
                         today=date(2025, 1, 1))
    assert r.validity_status == "VALID" and r.valid_until == "2026-05-11"
    assert r.fields["valid_to"].normalized == "2026-05-11"
    assert r.fields["bn_number"].grounding == EXACT and r.fields["bn_number"].confidence == 0.95
    assert r.fields["location"].value is None and r.fields["location"].confidence is None
    assert not [i for i in r.issues if i["severity"] != "INFO"]


def test_expired_document_is_critical():
    r = extract_document(_pages(), "DTI_BN_CERT", FakeVision(DTI_VALUES, "DTI_BN_CERT"),
                         today=date(2026, 10, 6))
    assert r.validity_status == "EXPIRED"
    expired = [i for i in r.issues if i["code"] == "EXPIRED"][0]
    assert expired["severity"] == "CRITICAL" and "148 days ago" in expired["message"]


def test_wrong_digit_is_flagged_not_confirmed():
    values = {**DTI_VALUES, "bn_number": "1234561"}
    r = extract_document(_pages(), "DTI_BN_CERT", FakeVision(values, "DTI_BN_CERT"),
                         today=date(2025, 1, 1))
    f = r.fields["bn_number"]
    assert f.grounding == CONFLICT and f.confidence < 0.5
    assert any(i["code"] == "OCR_CONFLICT" for i in f.issues)


def test_type_mismatch_re_extracts_with_right_fields():
    fake = FakeVision(DTI_VALUES, "DTI_BN_CERT")
    r = extract_document(_pages(), "MAYORS_PERMIT", fake, today=date(2025, 1, 1))
    assert len(fake.calls) == 2 and "DTI_BN_CERT" in fake.calls[1]
    assert r.document_type == "DTI_BN_CERT" and "bn_number" in r.fields
    assert any(i["code"] == "TYPE_MISMATCH" for i in r.issues)


def test_missing_required_and_invalid_tin():
    fake = FakeVision({"tin": "12-34", "taxpayer_name": None}, "BIR_2303")
    r = extract_document(_pages("TIN 12-34"), "BIR_2303", fake, today=date(2025, 1, 1))
    codes = {(i["code"], i["field"]) for i in r.issues}
    assert ("INVALID_TIN", "tin") in codes and ("MISSING_REQUIRED", "taxpayer_name") in codes
    assert r.fields["tin"].confidence == 0.2


def test_poor_page_lowers_confidence():
    r = extract_document(_pages(quality="POOR"), "DTI_BN_CERT",
                         FakeVision(DTI_VALUES, "DTI_BN_CERT"), today=date(2025, 1, 1))
    assert r.fields["bn_number"].confidence == round(0.95 * 0.75, 2)


def test_mayors_permit_expires_end_of_permit_year():
    text = "MAYOR'S PERMIT Series of 2025 Business Name EXAMPLE MARKET Permit No. 0420"
    fake = FakeVision({"business_name": "EXAMPLE MARKET", "owner_name": "JUAN DELA CRUZ",
                       "permit_number": "0420", "permit_year": "2025"}, "MAYORS_PERMIT")
    r = extract_document(_pages(text), "MAYORS_PERMIT", fake, today=date(2026, 1, 5))
    assert r.validity_status == "EXPIRED" and r.valid_until == "2025-12-31"


def test_malformed_output_retried_then_fails():
    class Broken:
        name, model = "broken", "x"

        def __init__(self):
            self.n = 0

        def extract(self, images, prompt, schema):
            self.n += 1
            return ModelOutput({"oops": True}, "x")

    broken = Broken()
    with pytest.raises(ExtractionError):
        extract_document(_pages(), "DTI_BN_CERT", broken, today=date(2025, 1, 1))
    assert broken.n == 2


def test_large_images_are_downscaled_for_the_model():
    from app.extraction.extract import MAX_IMAGE_SIDE, prepare_image
    big = cv2.imencode(".jpg", np.full((4000, 3000, 3), 200, np.uint8))[1].tobytes()
    out = cv2.imdecode(np.frombuffer(prepare_image(big), np.uint8), cv2.IMREAD_COLOR)
    assert max(out.shape[:2]) == MAX_IMAGE_SIDE


# ---- fixes found by the first real benchmark (gpt-6.1-sol vs gpt-6-luna) ----

BIR_TEXT = """CERTIFICATE OF REGISTRATION TIN & BRANCH CODE 003-500-318-00161 NAME OF TAXPAYER
EXAMPLE CORP. REGISTERING OFFICE Head Office X Branch REGISTERED ADDRESS LOT 20, BLOCK 23
VITO CRUZ EXT., SAN ANTONIO 1203 CITY OF MAKATI"""


def _bir(values):
    base = {"tin": "003-500-318-00161", "taxpayer_name": "EXAMPLE CORP.",
            "registered_address": "LOT 20, BLOCK 23 VITO CRUZ EXT., SAN ANTONIO 1203 CITY "
                                  "OF MAKATI",
            "taxpayer_type": None}
    return extract_document(_pages(BIR_TEXT), "BIR_2303",
                            FakeVision({**base, **values}, "BIR_2303"), today=date(2026, 1, 1))


def test_checkbox_contradicting_branch_code_is_critical():
    r = _bir({"registering_office": "Head Office"})  # luna's real mistake
    f = r.fields["registering_office"]
    assert f.grounding == CONFLICT and f.confidence == 0.2
    assert any(i["code"] == "BRANCH_MISMATCH" and i["severity"] == "CRITICAL" for i in r.issues)


def test_checkbox_matching_branch_code_is_cross_checked():
    f = _bir({"registering_office": "Branch"}).fields["registering_office"]
    assert f.grounding == "CROSS_CHECKED" and f.confidence == 0.9 and not f.issues


def test_head_office_tin_confirms_head_office():
    r = extract_document(_pages("TIN 601-088-612-00000 Head Office Branch"), "BIR_2303",
                         FakeVision({"tin": "601-088-612-00000",
                                     "registering_office": "Head Office"}, "BIR_2303"),
                         today=date(2026, 1, 1))
    assert r.fields["registering_office"].grounding == "CROSS_CHECKED"


def test_wrong_number_inside_address_is_conflict_not_fuzzy():
    f = _bir({"registered_address": "LOT20, BLOCK21 VITO CRUZ EXT., SAN ANTONIO 1203 CITY OF "
                                    "MAKATI"}).fields["registered_address"]  # sol's real mistake
    assert f.grounding == CONFLICT


def test_code_labels_are_stripped():
    assert normalize("code", "No. 0420") == "0420"
    assert normalize("code", "#2025 02655") == "2025 02655"
    assert normalize("code", "NORTH-1") == "NORTH-1"  # not a label
    assert normalize("code", "No 0420") == "0420" and normalize("code", "NO:12") == "12"


def test_numbers_conflict_rule():
    from app.extraction.normalize import numbers_conflict
    assert numbers_conflict("LOT 20 BLOCK 21", "LOT 20 BLOCK 23")
    assert not numbers_conflict("PUROK 1 PUYPUY BAY", "PUROK 1 PUYPUY 4033 BAY")  # zip omitted
    assert not numbers_conflict("REAL ST", "REAL ST")
