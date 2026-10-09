"""Extract one document: vision model -> normalize -> ground -> validate -> per-field confidence."""

import re
from dataclasses import dataclass, field
from datetime import date

import cv2
import numpy as np

from app.extraction.fields import SPECS, FieldSpec, specs_for
from app.extraction.grounding import (
    CONFLICT,
    CROSS_CHECKED,
    EXACT,
    FUZZY,
    NOT_FOUND,
    UNVERIFIABLE,
    ground,
)
from app.extraction.normalize import alnum, normalize
from app.extraction.prompts import output_schema, user_prompt
from app.extraction.providers import ExtractionError, ModelOutput, VisionProvider
from app.extraction.validate import Issue, check_fields, check_validity

MAX_PAGES = 5
MAX_IMAGE_SIDE = 1568  # larger images cost more tokens without reading better

_GROUNDING_CONFIDENCE = {EXACT: 0.95, FUZZY: 0.8, NOT_FOUND: 0.5, UNVERIFIABLE: 0.5,
                         CONFLICT: 0.3}
_QUALITY_FACTOR = {"GOOD": 1.0, "FAIR": 0.9, "POOR": 0.75, "UNREADABLE": 0.6}


@dataclass
class PageInput:
    image_jpeg: bytes
    grounding_text: str
    quality_label: str = "GOOD"


@dataclass
class FieldResult:
    name: str
    kind: str
    value: str | None
    normalized: str | None
    evidence: str | None
    page: int | None
    grounding: str | None          # EXACT | FUZZY | CONFLICT | NOT_FOUND | None (no value)
    ocr_match: str | None
    confidence: float | None
    zoho_field: str | None
    issues: list[dict] = field(default_factory=list)


@dataclass
class ExtractionResult:
    expected_type: str
    model_type: str
    document_type: str
    fields: dict[str, FieldResult]
    issues: list[dict]
    validity_status: str
    valid_until: str | None
    model: str
    input_tokens: int
    output_tokens: int
    seconds: float
    calls: int
    other_fields: list[dict] = field(default_factory=list)  # every other labelled item


def prepare_image(jpeg: bytes) -> bytes:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ExtractionError("Page image could not be decoded")
    h, w = img.shape[:2]
    if max(h, w) > MAX_IMAGE_SIDE:
        f = MAX_IMAGE_SIDE / max(h, w)
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()


def _call(provider: VisionProvider, images: list[bytes], doc_type: str,
          specs: list[FieldSpec]) -> ModelOutput:
    prompt = user_prompt(doc_type, specs, len(images))
    schema = output_schema(specs)
    last: Exception | None = None
    for _ in range(2):  # one retry for malformed output
        out = provider.extract(images, prompt, schema)
        if isinstance(out.data, dict) and isinstance(out.data.get("fields"), dict):
            return out
        last = ExtractionError("Model output does not match the schema")
    raise last  # type: ignore[misc]


def _field_result(spec: FieldSpec, item: dict | None, pages: list[PageInput]) -> FieldResult:
    item = item if isinstance(item, dict) else {}
    value = item.get("value")
    value = str(value).strip() if value not in (None, "") else None
    page = item.get("page") if isinstance(item.get("page"), int) else None
    normalized = normalize(spec.kind, value)
    if value is None:
        return FieldResult(spec.name, spec.kind, None, None, None, None, None, None, None,
                           spec.zoho_field)
    # Ground against the stated page; fall back to all pages (models misnumber pages).
    texts = [pages[page - 1].grounding_text] if page and 0 < page <= len(pages) else []
    g = ground(value, spec.kind, "\n".join(texts)) if texts else None
    if g is None or g.status == NOT_FOUND:
        g = ground(value, spec.kind, "\n".join(p.grounding_text for p in pages))
    quality = pages[page - 1].quality_label if page and 0 < page <= len(pages) else \
        min((p.quality_label for p in pages), key=lambda q: _QUALITY_FACTOR.get(q, 0.6))
    confidence = _GROUNDING_CONFIDENCE[g.status] * _QUALITY_FACTOR.get(quality, 0.6)
    issues = []
    if spec.kind in ("date", "tin", "money") and normalized is None:
        confidence = 0.2
    if spec.pattern and not re.fullmatch(spec.pattern, alnum(value)):
        confidence = 0.2
        issues.append(Issue("INVALID_FORMAT", f"'{value}' does not have the expected format",
                            spec.name, "WARNING").as_dict())
    if g.status == CONFLICT:
        issues.append(Issue("OCR_CONFLICT", f"OCR read '{g.matched}' near this value; check "
                            "the image", spec.name, "WARNING").as_dict())
    elif g.status == NOT_FOUND:
        issues.append(Issue("NOT_IN_OCR_TEXT", "Value could not be confirmed by OCR; check "
                            "the image", spec.name, "INFO").as_dict())
    elif g.status == UNVERIFIABLE:
        issues.append(Issue("CHECKBOX_NOT_VERIFIED", "Checkbox: confirm which box is marked "
                            "on the image", spec.name, "INFO").as_dict())
    return FieldResult(spec.name, spec.kind, value, normalized,
                       (item.get("evidence") or None), page, g.status, g.matched,
                       round(confidence, 2), spec.zoho_field, issues)


def check_branch_code(results: dict[str, FieldResult]) -> Issue | None:
    """BIR 2303: TIN branch code 00000 = Head Office, anything else = Branch. Confirms (or
    contradicts) the Head Office/Branch checkbox, which OCR text alone cannot verify."""
    tin, office = results.get("tin"), results.get("registering_office")
    if not tin or not office or not tin.normalized or not office.normalized:
        return None
    branch = tin.normalized[-5:]
    expected = "HEAD OFFICE" if branch == "00000" else "BRANCH"
    if office.normalized == expected:
        office.grounding, office.confidence, office.issues = CROSS_CHECKED, 0.9, []
        return None
    office.grounding, office.confidence = CONFLICT, 0.2
    issue = Issue("BRANCH_MISMATCH", f"TIN branch code {branch} means "
                  f"{'Head Office' if branch == '00000' else 'Branch'}, but the "
                  f"'{office.value}' box was read as marked", "registering_office", "CRITICAL")
    office.issues = [issue.as_dict()]
    return issue


def extract_document(pages: list[PageInput], expected_type: str, provider: VisionProvider,
                     today: date) -> ExtractionResult:
    if not pages:
        raise ExtractionError("Document has no pages")
    pages = pages[:MAX_PAGES]
    images = [prepare_image(p.image_jpeg) for p in pages]

    doc_type, specs = expected_type, specs_for(expected_type)
    out = _call(provider, images, doc_type, specs)
    calls, tokens_in, tokens_out, seconds = 1, out.input_tokens, out.output_tokens, out.seconds
    model_type = out.data.get("document_type") or "OTHER"
    issues: list[Issue] = []

    if model_type != expected_type:
        issues.append(Issue("TYPE_MISMATCH", f"OCR classified {expected_type}, the vision model "
                            f"says {model_type}", None, "WARNING"))
        if model_type in SPECS:  # re-extract with the right field list
            doc_type, specs = model_type, specs_for(model_type)
            out = _call(provider, images, doc_type, specs)
            calls += 1
            tokens_in += out.input_tokens
            tokens_out += out.output_tokens
            seconds += out.seconds

    raw_fields = out.data.get("fields") or {}
    results = {s.name: _field_result(s, raw_fields.get(s.name), pages) for s in specs}
    if doc_type == "BIR_2303":
        if branch_issue := check_branch_code(results):
            issues.append(branch_issue)
        ocn, rdo = results.get("ocn"), results.get("rdo_code")
        if ocn and rdo and ocn.value and rdo.value and re.fullmatch(r"\d{3}RC\d{14}",
                                                                   alnum(ocn.value)):
            rdo_digits = re.sub(r"\D", "", rdo.value).zfill(3)
            if alnum(ocn.value)[:3] != rdo_digits:
                issues.append(Issue("OCN_RDO_MISMATCH", f"OCN starts with "
                                    f"{alnum(ocn.value)[:3]} but the RDO is {rdo_digits}", "ocn"))
    raw = {k: r.value for k, r in results.items()}
    normalized = {k: r.normalized for k, r in results.items()}
    issues += check_fields(specs, raw, normalized)
    validity = check_validity(doc_type, normalized, today)
    issues += validity.issues
    if notes := out.data.get("legibility_notes"):
        issues.append(Issue("LEGIBILITY_NOTE", str(notes)[:300], None, "INFO"))

    other = [{"label": str(o.get("label", "")).strip()[:120],
              "value": str(o.get("value", "")).strip()[:500], "page": o.get("page")}
             for o in (out.data.get("other_fields") or [])[:80]
             if isinstance(o, dict) and o.get("label") and o.get("value")]
    return ExtractionResult(
        other_fields=other,
        expected_type=expected_type, model_type=model_type, document_type=doc_type,
        fields=results, issues=[i.as_dict() for i in issues],
        validity_status=validity.status, valid_until=validity.valid_until, model=out.model,
        input_tokens=tokens_in, output_tokens=tokens_out, seconds=round(seconds, 2),
        calls=calls,
    )
