"""Prompt + JSON schema for the vision model. Versioned: change EXTRACTION_PROMPT_VERSION
whenever wording or fields change, so cached results are recomputed."""

from app.extraction.fields import FieldSpec

EXTRACTION_PROMPT_VERSION = "ph-docs-2"

DOCUMENT_TYPES = ["BIR_2303", "DTI_BN_CERT", "MAYORS_PERMIT", "SEC_CERT", "GIS",
                  "BARANGAY_CLEARANCE", "GOVERNMENT_ID", "FOOD_SAFETY_PERMIT", "OTHER"]

SYSTEM = """You read Philippine business documents (BIR Form 2303, DTI Business Name
certificates, Mayor's/Business Permits and similar) from page images and return their fields.

Rules:
- Copy each value exactly as printed (same spelling, punctuation, word order). Do not correct,
  translate, reformat or complete values.
- If a field is not visible or not legible, return null. Never guess or infer a value.
- For numbers and codes return only the value, without labels such as "No." or "#".
- Long digit strings (TIN, OCN, receipt numbers): copy every digit; count repeated zeros carefully.
- For checkbox fields, return the option whose box is marked (X or check), not just any printed
  option.
- "evidence" is the short printed text where you read the value (label + value), max 120 chars.
- "page" is the 1-based page number where the value appears.
- Report what the document actually is in document_type, even if it differs from the expected type.
- You only extract. You do not judge whether the customer should be approved."""


def field_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"type": ["string", "null"]},
            "evidence": {"type": ["string", "null"]},
            "page": {"type": ["integer", "null"]},
        },
        "required": ["value", "evidence", "page"],
        "additionalProperties": False,
    }


def output_schema(specs: list[FieldSpec]) -> dict:
    return {
        "type": "object",
        "properties": {
            "document_type": {"type": "string", "enum": DOCUMENT_TYPES},
            "fields": {
                "type": "object",
                "properties": {s.name: field_schema() for s in specs},
                "required": [s.name for s in specs],
                "additionalProperties": False,
            },
            "legibility_notes": {"type": ["string", "null"]},
        },
        "required": ["document_type", "fields", "legibility_notes"],
        "additionalProperties": False,
    }


def user_prompt(expected_type: str, specs: list[FieldSpec], page_count: int) -> str:
    lines = [f"The {page_count} image(s) are the page(s) of one document. It was classified as "
             f"{expected_type}. Extract these fields:"]
    lines += [f"- {s.name}: {s.description}" for s in specs]
    return "\n".join(lines)
