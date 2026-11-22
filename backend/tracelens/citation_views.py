"""Locate unchanged upstream quotes in original inputs or official views.

This is display metadata, never a filter on upstream triggers or attribution.
"""
TIERS = ("th1", "th2", "th3")
EVIDENCE_PROTOCOL = "official_views_v1"


def compression_views(payload, trace):
    pool = {}
    for key, value in (payload.get("step_compressions") or {}).items():
        try:
            step = int(key)
        except (TypeError, ValueError):
            continue
        if not 0 <= step < len(trace.messages) or not isinstance(value, dict):
            continue
        pool[str(step)] = {tier: value[tier] for tier in TIERS if isinstance(value.get(tier), str) and value[tier]}
    return pool


def detector_tier(trace, pool, step, focus):
    """Mirror only upstream Stage B's view selection, not its diagnosis logic.

    Current: th1 -> th2 -> th3. History: compress-role override, then
    distance <=2 / <=5; fallback order from _stage_common.pool_line.
    """
    views = pool.get(str(step), {})
    if step == focus:
        order = TIERS
    else:
        distance = abs(step - focus)
        desired = "th3" if trace.messages[step].compress_role == "compress" else "th1" if distance <= 2 else "th2" if distance <= 5 else "th3"
        order = tuple(dict.fromkeys((desired, "th2", "th3", "th1")))
    return next((tier for tier in order if views.get(tier)), None)


def locate_quote(quote, trace, *, preferred=None, through=None, exact_step=False, pool=None, focus=None):
    if isinstance(quote, dict):
        preferred = quote.get("step", preferred)
        quote = quote.get("quote") or quote.get("text") or ""
    quote = str(quote or "")
    evidence = {"quote": quote, "step": None, "matched": False, "original_matched": False,
                "source_kind": "unlocated"}
    if not quote:
        return evidence
    limit = min(len(trace.messages) - 1, through) if type(through) is int else len(trace.messages) - 1
    order = ([preferred] if type(preferred) is int else []) + ([] if exact_step else list(range(limit + 1)))
    steps = [step for step in dict.fromkeys(order) if type(step) is int and 0 <= step <= limit]
    for step in steps:
        start = trace.messages[step].content.find(quote)
        if start >= 0:
            return {**evidence, "step": step, "start": start, "end": start + len(quote),
                    "matched": True, "original_matched": True, "source_kind": "original"}
    if not exact_step:
        for source in trace.runtime_inputs:
            if source.available_from <= limit and quote in source.content:
                return {**evidence, "source_id": source.source_id, "matched": True,
                        "original_matched": True, "source_kind": "runtime"}
        for source_id, text in (("task_description", trace.task_description),
                                ("agent_framework_description", trace.agent_framework_description)):
            if quote in text:
                return {**evidence, "source_id": source_id, "matched": True,
                        "original_matched": True, "source_kind": "task" if source_id == "task_description" else "framework"}
    pool = pool or {}
    matches = []
    for step in steps:
        tiers = (detector_tier(trace, pool, step, focus),) if type(focus) is int else TIERS
        for tier in tiers:
            text = pool.get(str(step), {}).get(tier, "")
            start = text.find(quote)
            if start >= 0:
                matches.append({"step": step, "tier": tier, "start": start, "end": start + len(quote)})
    if matches:
        return {**evidence, "step": matches[0]["step"], "matched": True, "source_kind": "compressed",
                "view_matches": matches,
                "view_scope": "detector_input" if type(focus) is int else "compression_pool"}
    return evidence


def summarize_evidence(triggers, instances):
    records = [t[key] for t in triggers for key in ("commitment", "reference")]
    records += [e for item in instances for e in item["lifecycle_evidence"]]
    return {"total": len(records), "original": sum(e["original_matched"] for e in records),
            "compressed": sum(e["source_kind"] == "compressed" for e in records),
            "unlocated": sum(not e["matched"] for e in records)}
