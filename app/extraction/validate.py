"""Document-level rules: required fields, formats, validity periods and expiry."""

from dataclasses import dataclass, field
from datetime import date

from app.extraction.fields import FieldSpec
from app.extraction.normalize import parse_date


@dataclass
class Issue:
    code: str
    message: str
    field: str | None = None
    severity: str = "WARNING"   # INFO | WARNING | CRITICAL

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "field": self.field,
                "severity": self.severity}


@dataclass
class Validity:
    status: str                 # VALID | EXPIRED | NOT_YET_VALID | NO_EXPIRY | UNKNOWN
    valid_until: str | None = None
    issues: list[Issue] = field(default_factory=list)


def _d(values: dict, name: str) -> date | None:
    v = values.get(name)
    return date.fromisoformat(v) if v else None


def check_fields(specs: list[FieldSpec], raw: dict, normalized: dict) -> list[Issue]:
    issues = []
    for spec in specs:
        value = raw.get(spec.name)
        if spec.required and not value:
            issues.append(Issue("MISSING_REQUIRED", f"{spec.name} not found on the document",
                                spec.name, "WARNING"))
            continue
        if value and normalized.get(spec.name) is None and spec.kind in ("date", "tin", "money"):
            issues.append(Issue(f"INVALID_{spec.kind.upper()}",
                                f"{spec.name} '{value}' is not a valid {spec.kind}", spec.name,
                                "CRITICAL" if spec.kind == "tin" else "WARNING"))
    return issues


def check_validity(document_type: str, normalized: dict, today: date) -> Validity:
    if document_type == "DTI_BN_CERT":
        start, end = _d(normalized, "valid_from"), _d(normalized, "valid_to")
        issues = []
        if start and end:
            years = (end - start).days / 365.25
            if not 4.9 <= years <= 5.1:
                issues.append(Issue("UNUSUAL_VALIDITY_PERIOD",
                                    f"DTI validity is {years:.1f} years; expected 5", "valid_to"))
        return _status(start, end, today, issues)

    if document_type == "MAYORS_PERMIT":
        end = _d(normalized, "valid_until")
        year = normalized.get("permit_year")
        if end is None and year and str(year).isdigit():
            end = date(int(year), 12, 31)  # permits run to Dec 31 of their year
        return _status(_d(normalized, "date_issued"), end, today, [])

    if document_type == "GOVERNMENT_ID":
        end = _d(normalized, "expiry_date")
        if end is None and "TIN" in (normalized.get("id_type") or ""):
            return Validity("NO_EXPIRY")  # BIR TIN IDs do not expire
        return _status(_d(normalized, "issue_date"), end, today, [])

    if document_type == "BIR_2303":
        # The COR does not expire; registration must simply exist.
        return Validity("VALID" if normalized.get("tin") else "UNKNOWN")

    return Validity("UNKNOWN")


def _status(start: date | None, end: date | None, today: date, issues: list[Issue]) -> Validity:
    if end is None:
        return Validity("UNKNOWN", None, issues)
    if end < today:
        issues.append(Issue("EXPIRED", f"Expired on {end.isoformat()} "
                                       f"({(today - end).days} days ago)", None, "CRITICAL"))
        return Validity("EXPIRED", end.isoformat(), issues)
    if start and start > today:
        issues.append(Issue("NOT_YET_VALID", f"Valid only from {start.isoformat()}"))
        return Validity("NOT_YET_VALID", end.isoformat(), issues)
    return Validity("VALID", end.isoformat(), issues)


def parse_iso(value: str | None) -> date | None:
    return parse_date(value) if value else None
