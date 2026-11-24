"""Input/output adapter. Algorithm decisions stay in the pinned upstream code."""
import hashlib
import json
from pathlib import Path
from .models import Trajectory
from ._vendor.trajdebug.data_processing.schema import validate_unified
from .citation_views import EVIDENCE_PROTOCOL, compression_views, locate_quote, summarize_evidence

UPSTREAM_COMMIT = "5b04f98b1995ec758d9aba70940d17bb7d2d169b"
UPSTREAM_URL = "https://github.com/THU-KEG/TrajDebug"
VENDOR = Path(__file__).parent / "_vendor" / "trajdebug"
ENGINE_VERSION = "trajdebug-official-" + UPSTREAM_COMMIT[:7]
STAGES = [("stage_a", "多粒度压缩", 8, 25), ("stage_b", "错误触发检测", 25, 55),
          ("phase1", "错误实例聚类", 55, 65), ("phase2", "生命周期判断", 65, 88),
          ("final", "关键失败归因", 88, 100)]


def verify_upstream():
    manifest = json.loads((VENDOR / "UPSTREAM.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        content = (VENDOR / relative).read_bytes()
        if hashlib.sha256(content).hexdigest() == expected:
            continue
        # Older Windows checkouts can retain CRLF after pulling .gitattributes.
        if b"\r\n" in content and hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest() == expected:
            continue
        raise ValueError("TrajDebug 官方源码完整性检查失败：" + relative)
    return manifest


def to_unified(trace: Trajectory):
    if trace.outcome == "unknown":
        raise ValueError("请在导入时明确轨迹运行结果（失败或成功），再运行 TrajDebug")
    messages = []
    for message in trace.messages:
        item = message.model_dump(exclude={"source_step", "source_refs"}, exclude_none=True)
        for source in trace.runtime_inputs:
            if source.available_from == message.step and source.available_from > 0:
                item["content"] += f"\n\n[可见运行时输入 {source.source_id}]\n{source.content}"
        messages.append(item)
    task = trace.task_description
    for source in trace.runtime_inputs:
        if source.available_from == 0:
            task += f"\n\n[可见运行时输入 {source.source_id}]\n{source.content}"
    unified = {"messages": messages, "metadata": {
        "dataset": trace.dataset, "task_id": trace.task_id, "task_description": task,
        "reward": int(trace.outcome == "succeeded"), "annotation": {},
        "extra": {"agent_framework_description": trace.agent_framework_description}}}
    if trace.otel is not None or trace.input_format == "deepagent":
        validate_unified(unified)
    return unified


def attribution_fields(critical, instances, step):
    """Project the category and supporting instance of the canonical pick."""
    source = critical.get('unified_source')
    category = critical.get('unified_taxonomy_tag')
    chosen = None
    if source == 'llm_pick':
        llm_step = critical.get('llm_pick_step')
        if llm_step is None:
            llm_step = critical.get('llm_unified_critical_step')
        aligned = step is not None and (llm_step is None or llm_step == step)
        if 'unified_critical_taxonomy_tag' in critical or 'llm_pick_taxonomy_tag' in critical:
            # The legacy tag survives a modern LLM override and can describe a different step.
            category = critical.get('unified_critical_taxonomy_tag') or (
                critical.get('llm_pick_taxonomy_tag') if aligned else None)
        elif not aligned or critical.get('chain_min_unified_critical_step') not in (None, step):
            category = None
        if aligned and (instance_id := critical.get('llm_pick_supporting_instance_id')) is not None:
            chosen = next((i for i in instances if i.get('instance_id') == instance_id), None)
    elif source == 'rescue':
        category = critical.get('rescue_category')
    elif step is not None:
        channel = critical.get('unified_channel')
        candidates = [i for i in instances if i.get('qualified_origin_step', i.get('origin_step')) == step
                      and not i.get('exploration_suppressed') and (not channel or i.get('attribution') == channel)
                      and (not category or i.get('qualified_origin_taxonomy_tag') == category)
                      and (source != 'chain_min_v2' or i.get('terminal_relevant'))]
        if len(candidates) == 1:
            chosen = candidates[0]
    return {'category': category or '未提供分类', 'critical_instance_id': chosen['id'] if chosen else None}


def previous_diagnostic_step(trace, raw_step):
    """Find the nearest preceding message that can carry an agent decision.

    The upstream final selector occasionally points at a user or tool message.
    That index cannot be shown as a critical agent step, but the preceding
    non-user message is the causal action for the observed response in the
    input contract.  This projection leaves the upstream output untouched.
    """
    if type(raw_step) is not int or raw_step <= 0:
        return None
    for message in reversed(trace.messages[:raw_step]):
        if message.role != "user" and message.judgable is not False:
            return message.step
    return None


def present_report(trace, outputs, *, model, usage, run_id=None, mode="live", stage_metrics=None):
    final = outputs.get("final") or {}
    views = compression_views(outputs.get("stage_a") or {}, trace)
    raw_triggers = (outputs.get("stage_b") or {}).get("step_triggers") or final.get("step_triggers") or []
    triggers = []
    for index, raw in enumerate(raw_triggers):
        step = raw.get("step")
        valid = type(step) is int and 0 <= step < len(trace.messages)
        def quote_evidence(key, preferred=None):
            return locate_quote(raw.get(key), trace, preferred=preferred, through=step,
                                exact_step=key == "wrong_content_quote", pool=views,
                                focus=step if raw.get("attribution") != "env" else None) if valid else {
                                    "quote": str(raw.get(key) or ""), "step": None, "matched": False,
                                    "original_matched": False, "source_kind": "unlocated"}
        triggers.append({"id": f"E{index + 1:03}", "step": step, "category": raw.get("category", ""),
                         "taxonomy_tag": raw.get("taxonomy_tag", ""), "attribution": raw.get("attribution", "agent"),
                         "description": raw.get("confidence_reasoning", ""), "confidence": raw.get("confidence", ""),
                         "commitment": quote_evidence("wrong_content_quote", step), "reference": quote_evidence("reference_quote")})
    merged = final.get("full_instance_states")
    if merged is None:
        states = {i["instance_id"]: i for i in (outputs.get("phase2") or {}).get("instance_states", [])}
        merged = [{**i, **states.get(i["instance_id"], {})} for i in (outputs.get("phase1") or {}).get("instances", [])]
    instances = []
    for raw in merged:
        fixed = str(raw.get("fix_status", "")).startswith("fixed_at_step_")
        chain = bool(raw.get("chain_membership"))
        state = ("unknown" if raw.get("parse_failed") else "exploration" if raw.get("exploration_suppressed") else
                 "costly_resolution" if fixed and chain else "clean_resolution" if fixed else
                 "manifest_active" if chain else "latent_active")
        evidence = [locate_quote(raw[key], trace, pool=views) for key in ("fix_evidence_quote", "state_evidence_quote") if raw.get(key)]
        instances.append({**raw, "id": f"I{raw['instance_id']}",
                          "object": raw.get("what_is_being_violated") or raw.get("error_content") or "错误实例",
                          "trigger_ids": [f"E{i + 1:03}" for i in raw.get("trigger_indices", []) if type(i) is int and 0 <= i < len(triggers)],
                          "error_state": state, "terminal_relevant": chain, "lifecycle_evidence": evidence,
                          "explanation": raw.get("chain_explanation") or raw.get("merged_reasoning") or ""})
    critical = final.get("critical_error") or {}
    raw_step = critical.get("unified_critical_step")
    valid_step = type(raw_step) is int and 0 <= raw_step < len(trace.messages) and trace.messages[raw_step].role != "user" and trace.messages[raw_step].judgable is not False
    step = raw_step if valid_step else None
    projected_previous_step = None
    if step is None:
        projected_previous_step = previous_diagnostic_step(trace, raw_step)
        step = projected_previous_step
    fields = attribution_fields(critical, instances, step)
    chosen = next((i for i in instances if i['id'] == fields['critical_instance_id']), None)
    rationale = critical.get("llm_pick_rationale") or critical.get("agent_chain_explanation") or critical.get("env_chain_explanation") or ""
    if not rationale and chosen:
        rationale = chosen["explanation"]
    if not rationale:
        rationale = "业务运行成功，无需进行失败归因。" if trace.outcome == "succeeded" else "本次诊断未定位到关键失败步骤。"
    warnings = []
    if raw_step is not None and not valid_step:
        if projected_previous_step is None:
            warnings.append("诊断返回的关键步骤无法定位到可诊断消息，已标记为未确定。")
        else:
            warnings.append(f"诊断返回的关键步骤位于非可诊断消息，已映射到前一条可诊断消息（步骤 {projected_previous_step}）。")
    if any(i.get("parse_failed") for i in instances):
        warnings.append("部分错误实例的状态无法确定，请结合轨迹原文复核。")
    summary = summarize_evidence(triggers, instances)
    unmatched = summary["unlocated"]
    if unmatched:
        warnings.append(f"{unmatched} 处引用尚未定位到轨迹原文或压缩视图，请核对引用来源。")
    suggestion = critical.get("llm_pick_fix_suggestion") or ""
    return {"schema_version": 2, "algorithm": "TrajDebug", "engine_version": ENGINE_VERSION,
            "evidence_protocol": EVIDENCE_PROTOCOL, "evidence_summary": summary, "compression_views": views,
            "input_sha256": hashlib.sha256(trace.input_json().encode()).hexdigest(),
            "provenance": {"repository": UPSTREAM_URL, "commit": UPSTREAM_COMMIT, "implementation": "vendored_unmodified", "runner": "official_five_stage", "run_id": run_id},
            "mode": mode, "model": model, "triggers": triggers, "instances": instances,
            "attribution": {"critical_step": step, **fields,
                            "summary": rationale, "reasoning": rationale, "fix_suggestions": [suggestion] if suggestion else [],
                            "confidence": "unrated", "source": critical.get("unified_source")},
            "official_output": final, "warnings": warnings, "usage": usage,
            "stage_metrics": stage_metrics or [], "candidate_ids": [i["id"] for i in instances if i["terminal_relevant"]]}
