"""Resolve workbench review status from human decisions and engine agreement."""


AGREEMENT_STATES = {"aligned", "same_step"}
UNSETTLED_STATES = {"different", "unresolved", "incomplete"}


def review_status(comparison_state, decision=None):
    if decision:
        return decision
    return "waived" if comparison_state in AGREEMENT_STATES else None


def job_review_status(job, reviews):
    if job["status"] != "completed":
        return None
    comparison = (job.get("result") or {}).get("comparison") or {}
    decision = reviews[0]["data"].get("decision") if reviews else None
    return review_status(comparison.get("state"), decision)
