"""Resolve workbench review status from human decisions and engine agreement."""


AGREEMENT_STATES = {"aligned", "same_step"}
UNSETTLED_STATES = {"different", "unresolved", "incomplete"}


def active_reviews(history):
    """Return the decisions since the latest reset event."""
    for index, review in enumerate(history):
        if review["data"].get("decision") == "reset":
            return history[:index]
    return history


def valid_review_step(step, messages, algorithm):
    if step is None:
        return True
    if type(step) is not int or not 0 <= step < len(messages):
        return False
    message = messages[step]
    return algorithm in {"AgentDebugX", "CHIEF"} or (message["role"] != "user" and message.get("judgable") is not False)


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
