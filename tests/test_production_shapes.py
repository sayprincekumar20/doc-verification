"""Shapes of real production Account / Contact records (values invented): Contact_of is a
multi-select list, Account_Primary_Contact a boolean, Mobile like '639XXXXXXXXX', Billing_City1
a lookup, Billing_Province the long NCR label."""

from datetime import date

from app.assessment.assess import DocInput, assess
from app.assessment.matching import SAME, match_address
from app.pipeline.collect import _contact_summary

CONTACT = {"id": "1001", "Full_Name": "ANA REYES", "First_Name": "ANA", "Last_Name": "REYES",
           "Email": "ana@examplecafe.ph", "Mobile": "639171234567", "Phone": None,
           "Contact_of": ["Accounting"], "Contact_Type": [], "Contact_Category": "Trader",
           "Position_Role": None, "Account_Primary_Contact": True,
           "Vendor_Primary_Contact": False, "Account_Name": {"id": "2001", "name": "X"}}


def test_contact_summary_handles_lists_and_primary_flag():
    s = _contact_summary(CONTACT)
    assert s["primary"] is True
    assert s["roles"] == {"Contact_of": "Accounting", "Contact_Category": "Trader"}
    vendor_only = _contact_summary({**CONTACT, "Account_Primary_Contact": False,
                                    "Vendor_Primary_Contact": True})
    assert vendor_only["primary"] is False


def test_ncr_label_and_abbreviated_address_match():
    zoho = ("2nd Flr EXAMPLE Building L2, B3, P1 Santiago St. Magallanes, Makati, "
            "National Capital Region (Manila)")
    bir = ("2ND FLOOR EXAMPLE BUILDING LOT 2 BLOCK 3 PHASE 1 SANTIAGO STREET MAGALLANES 1232 "
           "CITY OF MAKATI NCR FOURTH DISTRICT")
    assert match_address(zoho, bir) == SAME
    assert match_address(zoho, bir.replace("LOT 2", "LOT 4")) != SAME


def _f(v):
    return {"value": v, "normalized": v, "confidence": 0.98, "grounding": "EXACT"}


def test_production_shaped_corporation_passes_every_activation_check():
    snapshot = {
        "Account_Name": "Example Cafe Inc. (Makati) - Commissary",
        "Credit_Terms": "Cash On Delivery", "Invoice_Company_Name": "Example Cafe, Inc.",
        "Tax_Identification_Number_TIN": "600-111-222-00000",
        "Billing_Street": "2nd Flr EXAMPLE Building L2, B3, P1 Santiago St. Magallanes",
        "Billing_City": "Makati", "Billing_City1": {"name": "Makati", "id": "1"},
        "Billing_Province": "National Capital Region (Manila)", "Billing_Code": None,
        "_contacts": [_contact_summary(CONTACT)]}
    bir = DocInput("b", "2303.jpg", "BIR_2303", {
        "tin": _f("600-111-222-00000"), "taxpayer_name": _f("EXAMPLE CAFE, INC."),
        "taxpayer_type": _f("DOMESTIC CORPORATION"),
        "registered_address": _f("2ND FLOOR EXAMPLE BUILDING LOT 2 BLOCK 3 PHASE 1 SANTIAGO "
                                 "STREET MAGALLANES 1232 CITY OF MAKATI NCR")}, "VALID")
    checks = {c["key"]: c for c in assess(snapshot, [bir], date(2026, 10, 28))["activation_checks"]}
    assert {k: c["status"] for k, c in checks.items()} == {
        "payment_terms": "PASS", "accounting_contact": "PASS", "tin": "PASS",
        "invoice_name": "PASS", "billing_address": "PASS"}
    assert "+63 917 123 4567" in checks["accounting_contact"]["detail"]


def test_email_is_checked_for_format_only():
    from app.assessment.assess import email_format_ok
    for ok in ("ana@examplecafe.ph", "donna.cruz@gmail.com", "fakeemail@gmail.com",
               "na@gmail.com", "accounting+ap@shop.com.ph", " spaced@shop.ph "):
        assert email_format_ok(ok), ok
    for bad in ("not an email", "ana@", "@shop.ph", "ana@shop", "ana shop@x.ph", "", None,
                "ana@@shop.ph"):
        assert not email_format_ok(bad), bad


def test_production_contact_of_spelling_accouting_counts():
    from app.assessment.assess import activation_checks
    contact = _contact_summary({**CONTACT, "Contact_of": ["Accouting"],
                                "Account_Primary_Contact": False})
    checks = activation_checks({"Credit_Terms": "Cash On Delivery", "_contacts": [contact]},
                               {"tin": [], "registered_name": [], "business_name": [],
                                "address": []}, [], "CORPORATION")
    assert next(c for c in checks if c["key"] == "accounting_contact")["status"] == "PASS"
