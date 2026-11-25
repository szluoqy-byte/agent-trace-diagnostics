"""Resolve workbench review status from human decisions and engine agreement."""


def review_status(comparison_state, decision=None):
    if decision:
        return decision
    return "waived" if comparison_state == "aligned" else None


def job_review_status(job, reviews):
    if job["status"] != "completed":
        return None
    comparison = (job.get("result") or {}).get("comparison") or {}
    decision = reviews[0]["data"].get("decision") if reviews else None
    return review_status(comparison.get("state"), decision)
