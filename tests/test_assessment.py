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


ACCOUNTING = {"name": "MARIA SANTOS", "email": "maria@juanmeat.ph", "mobile": "0917 123 4567",
              "roles": {"Contact_of": "Accounting"}}
SNAPSHOT = {"Account_Name": "JUAN MEAT SHOP", "Invoice_Company_Name": "JUAN MEAT SHOP",
            "Owner_Name": "SALES REP NAME", "Owner": {"name": "SALES REP NAME"},
            "Billing_Street": "Purok 1 Brgy. Masagana, Bay", "Credit_Terms": "COD",
            "_contacts": [ACCOUNTING]}


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
    assert p["Type_of_Business_Organization"]["action"] == "FILL"
    assert p["Type_of_Business_Organization"]["proposed_value"] == "Corporation"
    assert "Owner_Name" not in p  # a corporation's name is not a person
    assert p["Account_Name"]["action"] == "DIFFERS"  # brand vs registered name, flagged only
    assert p["Invoice_Company_Name"]["proposed_value"] == "EXAMPLE FOODS INC."  # as printed


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


def test_match_with_zoho_never_needs_attention():
    low = bir()
    low.fields["tin"] = f("123-456-789-00000", grounding="NOT_FOUND", confidence=0.45)
    a = assess({**SNAPSHOT, "Tax_Identification_Number_TIN": "123-456-789-00000"},
               [low, dti(), permit()], TODAY)
    p = by_field(a)["Tax_Identification_Number_TIN"]
    assert p["action"] == "MATCH" and p["needs_attention"] is False


def test_valid_until_source_is_explained():
    from app.assessment.assess import valid_until_source
    d = dti(until="2028-06-29")
    d.fields["valid_to"] = f("29 June 2028")
    assert valid_until_source(d) == "valid_to as printed: 29 June 2028"
    p = permit()
    p.issues = [{"code": "VALIDITY_DERIVED", "severity": "INFO",
                 "message": "No expiry printed; valid until 2026-12-31 "
                            "(Dec 31 of the date issued)"}]
    assert valid_until_source(p).startswith("derived: No expiry printed")
    assert valid_until_source(bir()) == "no expiry for this document type"
    a = assess(SNAPSHOT, [bir(), d, permit()], TODAY)
    data = {x["type"]: x for x in a["document_data"]}
    assert data["DTI_BN_CERT"]["valid_until_source"] == "valid_to as printed: 29 June 2028"
    req = {r["document_type"]: r for r in a["requirements"]}
    assert req["DTI_BN_CERT"]["valid_until_source"] == "valid_to as printed: 29 June 2028"


def test_required_document_without_printed_expiry_counts_as_valid():
    from app.assessment.assess import valid_until_source
    p = permit()
    p.validity_status, p.valid_until = "NO_EXPIRY", None
    p.issues = [{"code": "NO_EXPIRY_PRINTED", "severity": "WARNING",
                 "message": "No expiry date printed on this document (check the document)"}]
    a = assess(SNAPSHOT, [bir(), dti(), p], TODAY)
    assert a["recommendation"] == "ACTIVE"
    req = {r["document_type"]: r for r in a["requirements"]}
    assert req["MAYORS_PERMIT"]["status"] == "NO_EXPIRY"
    assert valid_until_source(p) == "no expiry date printed on this document"


def checks_of(a):
    return {c["key"]: c for c in a["activation_checks"]}


def test_activation_checks_all_met():
    c = checks_of(assess(SNAPSHOT, [bir(), dti(), permit()], TODAY))
    assert c["payment_terms"]["status"] == "PASS"
    assert c["accounting_contact"]["status"] == "PASS"
    assert c["invoice_name"]["status"] == "PASS"
    assert c["billing_address"]["status"] == "PASS"
    assert c["tin"]["status"] == "AFTER_UPDATE"          # empty in Zoho, filled from the BIR 2303


def test_not_cod_means_manual_review():
    a = assess({**SNAPSHOT, "Credit_Terms": "Net 30"}, [bir(), dti(), permit()], TODAY)
    assert a["recommendation"] == "MANUAL_REVIEW"
    assert "Payment Terms is COD" in a["reasons"][0]


def test_cod_spellings():
    from app.assessment.policy import is_cod
    assert is_cod("COD") and is_cod("Cash on Delivery") and is_cod("C.O.D".replace(".", ""))
    assert not is_cod("Net 30") and not is_cod("CODE 7") and not is_cod(None)


def test_accounting_contact_needs_name_mobile_and_real_email():
    bad = {**ACCOUNTING, "email": "fakeemail@gmail.com"}
    a = assess({**SNAPSHOT, "_contacts": [bad]}, [bir(), dti(), permit()], TODAY)
    c = checks_of(a)["accounting_contact"]
    assert c["status"] == "FAIL" and "real email" in c["detail"]
    other_role = {**ACCOUNTING, "roles": {"Contact_of": "Purchasing"}}
    c = checks_of(assess({**SNAPSHOT, "_contacts": [other_role]}, [bir()], TODAY))
    assert c["accounting_contact"]["detail"].startswith("no Contact with Contact of = Accounting")
    by_position = {**ACCOUNTING, "roles": {"Position_Role": "Accounting Manager"}}
    c = checks_of(assess({**SNAPSHOT, "_contacts": [by_position]}, [bir()], TODAY))
    assert c["accounting_contact"]["status"] == "PASS"


def test_primary_contact_counts_too():
    primary = {**ACCOUNTING, "roles": {"Contact_of": "Purchasing"}, "primary": True}
    c = checks_of(assess({**SNAPSHOT, "_contacts": [primary]}, [bir()], TODAY))
    assert c["accounting_contact"]["status"] == "PASS"
    assert c["accounting_contact"]["detail"].endswith("(primary contact)")
    assert "+63 917 123 4567" in c["accounting_contact"]["detail"]


def test_mobile_must_be_a_philippine_mobile():
    landline = {**ACCOUNTING, "mobile": "(02) 8123 4567"}
    c = checks_of(assess({**SNAPSHOT, "_contacts": [landline]}, [bir()], TODAY))
    assert c["accounting_contact"]["status"] == "FAIL"
    assert "Philippine mobile number (has (02) 8123 4567)" in c["accounting_contact"]["detail"]


def test_ph_mobile_formats():
    from app.assessment.policy import ph_mobile
    for v in ("09171234567", "0917 123 4567", "0917-123-4567", "+639171234567",
              "+63 917 123 4567", "639171234567", "9171234567"):
        assert ph_mobile(v) == "+63 917 123 4567", v
    for v in ("(02) 8123 4567", "0817 123 4567", "0917123456", "091712345678", "", None,
              "tel 0917 123 4567"):
        assert ph_mobile(v) is None, v


def test_tin_in_zoho_differing_from_bir_fails():
    a = assess({**SNAPSHOT, "Tax_Identification_Number_TIN": "999-999-999-00000"},
               [bir(), dti(), permit()], TODAY)
    assert checks_of(a)["tin"]["status"] in ("AFTER_UPDATE", "FAIL")
    assert checks_of(a)["tin"]["detail"] != ""


def _high(doc):
    for v in doc.fields.values():
        v.update(confidence=0.98, grounding="EXACT")
    return doc


def test_auto_activation_needs_every_check():
    from app.assessment.alerts import status_decision
    docs = [_high(bir()), _high(dti()), _high(permit())]
    a = assess({**SNAPSHOT, "Tax_Identification_Number_TIN": "123-456-789-00000"}, docs, TODAY)
    assert a["recommendation"] == "ACTIVE"
    assert status_decision(a, None, 0.95)[0] == "ACTIVATE"
    # same documents, but no Accounting contact: not activated, reason given
    a = assess({**SNAPSHOT, "Tax_Identification_Number_TIN": "123-456-789-00000",
                "_contacts": []}, docs, TODAY)
    decision, why = status_decision(a, None, 0.95)
    assert a["recommendation"] == "MANUAL_REVIEW" and decision == "NO_CHANGE"
    assert "Accounting or primary contact" in a["reasons"][0]
