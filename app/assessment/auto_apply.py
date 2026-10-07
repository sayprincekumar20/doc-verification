"""Decide which proposals may be written to Zoho without human review."""

from app.assessment import policy


def blocking_problems(assessment: dict) -> list[str]:
    """Critical problems that stop ALL automatic updates for the account. An expired document
    alone does not: the TIN on a valid BIR 2303 is still right when the permit has expired."""
    problems = [f"documents disagree on {c['attribute']}" for c in assessment["cross_document"]
                if c["status"] == "CONFLICT" and c["severity"] == "CRITICAL"]
    for d in assessment["documents"]:
        problems += [f"{d['file']}: {code}" for code in d.get("critical_issues", [])
                     if code != "EXPIRED"]
    return problems


def decide(proposal: dict, threshold: float, blockers: list[str]) -> tuple[bool, str]:
    field, action = proposal["zoho_field"], proposal["action"]
    rules = policy.AUTO_APPLY_RULES.get(field)
    if rules is None or action not in rules:
        return False, f"{action} on {field} always goes to review"
    if blockers:
        return False, "account has critical problems: " + "; ".join(blockers)
    if proposal.get("needs_attention"):
        return False, "proposal needs attention"
    conf = proposal.get("confidence") or 0
    if conf < threshold:
        return False, f"confidence {conf} below threshold {threshold}"
    if proposal.get("grounding") not in policy.AUTO_APPLY_GROUNDINGS:
        return False, f"value not confirmed by OCR (grounding {proposal.get('grounding')})"
    needed = rules[action]
    if proposal.get("agreeing_documents", 0) < needed:
        return False, f"{action} needs {needed} agreeing documents"
    if proposal.get("proposed_value") in (None, ""):
        return False, "no value to write"
    return True, f"confidence {conf}, {proposal.get('grounding')}, " \
                 f"{proposal.get('agreeing_documents')} document(s) agree"


def plan(assessment: dict, threshold: float) -> list[dict]:
    """One entry per FILL/CORRECT proposal: auto or review, with the reason."""
    blockers = blocking_problems(assessment)
    out = []
    for p in assessment["proposals"]:
        if p["action"] not in ("FILL", "CORRECT"):
            continue
        auto, reason = decide(p, threshold, blockers)
        out.append({"zoho_field": p["zoho_field"], "action": p["action"],
                    "current_value": p["current_value"], "new_value": p["proposed_value"],
                    "confidence": p["confidence"], "grounding": p.get("grounding"),
                    "agreeing_documents": p.get("agreeing_documents"), "sources": p["sources"],
                    "decision": "AUTO" if auto else "REVIEW", "reason": reason})
    return out
