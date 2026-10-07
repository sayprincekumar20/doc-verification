"""Phase 1D: matching rules, cross-document checks, Zoho proposals, recommendation.
Scenarios mirror real cases (anonymized)."""

from datetime import date

import pytest

from app.assessment.assess import DocInput, assess
from app.assessment.matching import (
    DIFFERENT,
    LIKELY_SAME,
    SAME,
    match_address,
    match_business,
    match_person,
    match_tin,
    natural_person_name,
)

TODAY = date(2026, 10, 7)


@pytest.mark.parametrize("a,b,expected", [
    ("Purok 1 Brgy. Masagana, Bay", "PUROK 1 MASAGANA 4033 BAY LAGUNA PHILIPPINES", SAME),
    ("LOT20, BLK23 RIZAL EXT., SAN ANTONIO 1203", "LOT 20, BLOCK 23 RIZAL EXT., SAN ANTONIO 1203",
     SAME),
    ("LOT 20, BLOCK 21 RIZAL EXT", "LOT 20, BLOCK 23 RIZAL EXT", DIFFERENT),
    ("REAL ST., SAMPAGUITA VILLAGE, BRGY76", "#10 SAMPAGUITA VILLAGE BARANGAY 76 6500", DIFFERENT),
])
def test_match_address(a, b, expected):
    assert match_address(a, b) == expected


@pytest.mark.parametrize("a,b,expected", [
    ("DELA CRUZ, JUAN", "JUAN DELA CRUZ", SAME),
    ("MARIA C. SANTOS", "MARIA SANTOS", SAME),                       # middle initial
    ("REYES, ANA DELOS SANTOS", "ANA REYES", LIKELY_SAME),           # middle name left out
    ("REYES, ANA DELOS SANTOS", "MARIA C. SANTOS", DIFFERENT),
    ("PEDRO PENDUKO JR.", "PEDRO PENDUKO", SAME),
])
def test_match_person(a, b, expected):
    assert match_person(a, b) == expected


def test_match_business_and_tin():
    assert match_business("JUAN'S MEAT SHOP", "JUAN MEAT SHOP") == SAME
    assert match_business("JUANS BISTRO AND GRILL", "JUAN'S BISTRO") == LIKELY_SAME
    assert match_business("SAMPLE BRAND MALL", "EXAMPLE FOODS INC.") == DIFFERENT
    assert match_tin("123-456-789-000", "123-456-789-00000") == SAME
    assert match_tin("123-456-789-00004", "123-456-789-00000") == DIFFERENT
    assert match_tin("123-456-789-00004", "123-456-789-00000", ignore_branch=True) == SAME
    assert natural_person_name("DELA CRUZ, JUAN") == "JUAN DELA CRUZ"


def f(value, normalized=None, grounding="EXACT", confidence=0.95):
    return {"value": value, "normalized": normalized or value, "grounding": grounding,
            "confidence": confidence}


def bir(tin="123-456-789-00000", name="DELA CRUZ, JUAN", trade="JUAN'S MEAT SHOP",
        ttype="SINGLE PROPRIETORSHIP ONLY (RESIDENT CITIZEN)", address="PUROK 1 MASAGANA 4033 BAY"):
    return DocInput("bir", "bir.jpg", "BIR_2303", {
        "tin": f(tin), "taxpayer_name": f(name), "trade_name": f(trade),
        "taxpayer_type": f(ttype), "registered_address": f(address),
        "line_of_business": f("RETAIL SALE OF MEAT")}, "VALID")


def dti(owner="JUAN DELA CRUZ", until="2028-05-11", status="VALID"):
    return DocInput("dti", "dti.jpg", "DTI_BN_CERT", {
        "owner_name": f(owner), "business_name": f("JUAN'S MEAT SHOP")}, status, until)


def permit(owner="JUAN DELA CRUZ", business="JUAN'S MEAT SHOP", until="2026-12-31",
           status="VALID"):
    return DocInput("permit", "permit.jpg", "MAYORS_PERMIT", {
        "owner_name": f(owner), "business_name": f(business),
        "business_address": f("PUROK 1 MASAGANA BAY")}, status, until)


SNAPSHOT = {"Account_Name": "JUAN MEAT SHOP", "Invoice_Company_Name": "JUAN MEAT SHOP",
            "Owner_Name": "SALES REP NAME", "Owner": {"name": "SALES REP NAME"},
            "Billing_Street": "Purok 1 Brgy. Masagana, Bay"}


def by_field(a):
    return {p["zoho_field"]: p for p in a["proposals"]}


def test_all_valid_and_consistent_is_active():
    a = assess(SNAPSHOT, [bir(), dti(), permit()], TODAY)
    assert a["recommendation"] == "ACTIVE"
    assert {c["attribute"]: c["status"] for c in a["cross_document"]}["owner_name"] == "CONSISTENT"
    p = by_field(a)
    assert p["Owner_Name"]["action"] == "CORRECT"
    assert p["Owner_Name"]["proposed_value"] == "JUAN DELA CRUZ"
    assert "sales rep" in p["Owner_Name"]["reason"]
    assert p["Tax_Identification_Number_TIN"]["action"] == "FILL"
    assert p["Type_of_Business_Organization"]["proposed_value"] == "Single Proprietorship"
    assert p["Invoice_Company_Name"]["action"] == "MATCH"
    assert p["Billing_Street"]["action"] == "MATCH"
    assert p["Business_Permit"]["action"] == "ATTACH"
    assert p["Owner_Name"]["confidence"] == 0.98  # three documents agree


def test_expired_required_documents_make_inactive():
    a = assess(SNAPSHOT, [bir(), dti(until="2026-05-11", status="EXPIRED"),
                          permit(until="2025-12-31", status="EXPIRED")], TODAY)
    assert a["recommendation"] == "INACTIVE"
    assert len([r for r in a["reasons"] if "expired" in r]) == 2
    assert "Business_Permit" not in by_field(a)  # expired documents are not attached


def test_documents_of_another_person_hold_everything():
    gov_id = DocInput("id", "id.png", "GOVERNMENT_ID", {
        "full_name": f("REYES, ANA DELOS SANTOS"), "tin": f("987-654-321-000")}, "NO_EXPIRY")
    a = assess({"Account_Name": "ANA REYES"},
               [permit(owner="MARIA C. SANTOS", business="MARIA SANTOS MEAT STALL"), gov_id],
               TODAY)
    assert a["recommendation"] == "MANUAL_REVIEW"
    owner = [c for c in a["cross_document"] if c["attribute"] == "owner_name"][0]
    assert owner["status"] == "CONFLICT" and owner["severity"] == "CRITICAL"
    p = by_field(a)
    assert p["Owner_Name"]["action"] == "REVIEW_CONFLICT"
    assert all(x["action"] not in ("FILL", "CORRECT", "ATTACH") for x in a["proposals"])
    assert p["Tax_Identification_Number_TIN"]["action"] == "HOLD"
    assert p["Tax_Identification_Number_TIN"]["proposed_value"] == "987-654-321-000"


def test_corporation_needs_sec_and_has_no_picklist_value():
    corp = bir(tin="009-111-222-00004", name="EXAMPLE FOODS INC.", trade="EXAMPLE FOODS INC.",
               ttype="DOMESTIC CORPORATION")
    a = assess({"Account_Name": "SAMPLE BRAND MALL"}, [corp], TODAY)
    assert a["business_form"] == "CORPORATION"
    assert a["recommendation"] == "MANUAL_REVIEW"
    assert any("SEC_CERT" in r for r in a["reasons"])
    p = by_field(a)
    assert p["Type_of_Business_Organization"]["action"] == "NO_PICKLIST_VALUE"
    assert "Owner_Name" not in p  # a corporation's name is not a person
    assert p["Account_Name"]["action"] == "DIFFERS"  # brand vs registered name, flagged only
    assert p["Invoice_Company_Name"]["proposed_value"] == "EXAMPLE FOODS INC"


def test_tin_conflict_between_bir_and_id_is_critical():
    gov_id = DocInput("id", "id.png", "GOVERNMENT_ID", {
        "full_name": f("DELA CRUZ, JUAN"), "tin": f("999-999-999-000")}, "NO_EXPIRY")
    a = assess(SNAPSHOT, [bir(), dti(), permit(), gov_id], TODAY)
    tin = [c for c in a["cross_document"] if c["attribute"] == "tin"][0]
    assert tin["status"] == "CONFLICT" and a["recommendation"] == "MANUAL_REVIEW"


def test_zoho_tin_that_differs_is_corrected():
    a = assess({**SNAPSHOT, "Tax_Identification_Number_TIN": "123-456-780-000"},
               [bir(), dti(), permit()], TODAY)
    assert by_field(a)["Tax_Identification_Number_TIN"]["action"] == "CORRECT"


def test_low_confidence_value_needs_attention():
    low = bir()
    low.fields["tin"] = f("123-456-789-00000", grounding="CONFLICT", confidence=0.3)
    p = by_field(assess(SNAPSHOT, [low, dti(), permit()], TODAY))["Tax_Identification_Number_TIN"]
    assert p["action"] == "FILL" and p["needs_attention"]


def test_critical_document_issue_forces_review():
    b = bir()
    b.issues = [{"code": "BRANCH_MISMATCH", "message": "x", "severity": "CRITICAL"}]
    assert assess(SNAPSHOT, [b, dti(), permit()], TODAY)["recommendation"] == "MANUAL_REVIEW"
