"""Bridge between the workbench trajectory and the AgentDebugX runtime."""

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from ..citation_views import locate_quote
from ..config import ROOT
from ..deepagent import obj, span_display_layout, span_kind
from ..llm import ModelError
from ..trajdebug import to_unified
from . import AGENTDEBUGX_COMMIT, AGENTDEBUGX_URL, AGENTDEBUGX_VERSION
from .agentdebugx_prompts import prompt_profile
from .agentdebugx_source import verify_agentdebugx


EVENT_TYPES = {"assistant": "llm.response", "tool": "tool.result", "user": "observation", "system": "observation"}


def deepagent_sources(trace):
    if not trace.deepagent:
        return {}
    spans = {span["span_id"]: span for span in trace.deepagent.spans}
    by_span = {}
    for message in trace.messages:
        span_id, _, _ = message.source_step.rpartition(":")
        if span_id in spans:
            by_span[span_id] = message.step
    sources, owners = {}, {}
    for span, _, _, _ in span_display_layout(spans.values()):
        span_id = span["span_id"]
        kind = span_kind(span, obj(span.get("attributes")))
        name = span.get("span_name") if kind == "AGENT" else owners.get(span.get("parent_id"))
        owners[span_id] = name
        if span_id not in by_span:
            continue
        parent_step = by_span.get(span.get("parent_id"))
        sources[by_span[span_id]] = (span_id, kind, name, parent_step)
    return sources


def to_agentdebugx(trace):
    unified = to_unified(trace)
    digest = hashlib.sha256(trace.input_json().encode()).hexdigest()
    trace_id = f"trace-{digest}"
    sources = deepagent_sources(trace)
    events = []
    for item, message in zip(unified["messages"], trace.messages, strict=True):
        role = item["role"]
        # Only an assistant message with an explicit OTel Agent name establishes
        # a distinct actor. Tool names and span titles do not.
        actor = message.name if trace.otel and role == "assistant" and message.name else "agent"
        event = {
            "event_id": f"step-{message.step}",
            "trace_id": trace_id,
            "step_index": message.step,
            "event_type": EVENT_TYPES[role],
            "agent_name": actor,
            "module": role,
            "metadata": {"role": role, "source_step": message.source_step},
            "input" if role in {"user", "system"} else "output": item["content"],
        }
        if source := sources.get(message.step):
            span_id, kind, owner_name, parent_step = source
            event["metadata"]["span_id"] = span_id
            event["metadata"]["span_kind"] = kind
            if isinstance(owner_name, str) and owner_name.strip():
                event["agent_name"] = owner_name.strip()
            if kind in {"AGENT", "STEP"}:
                event["event_type"] = "agent.step"
            if parent_step is not None:
                event["parent_event_id"] = f"step-{parent_step}"
        elif message.source_refs:
            event["metadata"]["source_refs"] = [reference.model_dump() for reference in message.source_refs]
        events.append(event)
    return {
        "trace_id": trace_id,
        "task_id": trace.task_id,
        "goal": unified["metadata"]["task_description"],
        "framework": trace.agent_framework_description,
        "metadata": {"outcome": trace.outcome},
        "events": events,
    }


def locate_engine_quote(trace, step, quote):
    evidence = locate_quote(quote, trace, preferred=step, exact_step=True)
    if evidence["matched"] or step is None or not quote.strip():
        return evidence
    # The upstream report can collapse whitespace when quoting an event.
    # Restore the actual source slice before it is used for UI highlighting.
    pattern = r"\s+".join(re.escape(part) for part in quote.split())
    matches = re.finditer(pattern, trace.messages[step].content)
    first = next(matches, None)
    if first is None or next(matches, None) is not None:
        return evidence
    return {**evidence, "quote": first.group(), "step": step, "start": first.start(), "end": first.end(),
            "matched": True, "original_matched": True, "source_kind": "original"}


def present_agentdebugx(trace, request, output, model, language):
    official = output["diagnosis"]
    by_event = {event["event_id"]: event["step_index"] for event in request["events"]}
    root_event = official.get("root_cause_event_id")
    step = by_event.get(root_event)
    root = next((finding for finding in official.get("findings", []) if finding.get("event_id") == root_event), {})
    category = (root.get("failure_mode") or {}).get("mode_id", "")
    evidence = []
    for ref in output["evidence"]:
        mapped = by_event.get(ref["event_id"])
        evidence.append(locate_engine_quote(trace, mapped, ref["quote"]))
    recorded = sum(item["original_matched"] for item in evidence)
    summary = official.get("summary") or ""
    warnings = []
    if trace.outcome == "failed" and step is None:
        warnings.append("AgentDebugX 未定位到可映射的根因事件。")
    if trace.outcome == "failed" and not any(item["original_matched"] and item["step"] == step for item in evidence):
        warnings.append("AgentDebugX 根因步骤缺少已定位的原文引用。")
    return {
        "schema_version": 2,
        "algorithm": "AgentDebugX",
        "engine_version": f"agentdebugx-{AGENTDEBUGX_VERSION}",
        "mode": "live",
        "model": model,
        "prompt_language": language,
        "input_sha256": request["trace_id"].removeprefix("trace-"),
        "provenance": {"repository": AGENTDEBUGX_URL, "commit": AGENTDEBUGX_COMMIT,
                       "implementation": "vendored_source", "runner": "deepdebug",
                       "prompt_profile": prompt_profile(language)},
        "task_outcome": trace.outcome,
        "triggers": [],
        "instances": [],
        "attribution": {
            "critical_step": step, "critical_instance_id": None, "category": category,
            "summary": summary, "reasoning": summary, "source": "deepdebug",
            "fix_suggestions": official.get("suggestions", []), "confidence": "unrated",
        },
        "root_cause_agent": official.get("root_cause_agent"),
        "evidence": evidence,
        "evidence_summary": {"total": len(evidence), "original": recorded,
                             "compressed": 0, "unlocated": len(evidence) - recorded},
        "warnings": warnings,
        "usage": output["usage"],
        "official_output": official,
        "engine_detection": output["detection"],
    }


async def diagnose_agentdebugx(trace, settings, progress):
    if not settings.ready:
        raise ModelError("当前分析的模型连接不可用，请检查系统设置后重新分析。")
    verify_agentdebugx()
    request = to_agentdebugx(trace)
    payload = {
        "trajectory": request, "outcome": trace.outcome,
        "settings": {key: getattr(settings, key) for key in
                     ("model", "base_url", "prompt_language", "timeout", "max_retries", "max_tokens")},
    }
    env = {**os.environ, "OPENAI_API_KEY": settings.api_key or "EMPTY",
           "TRACELENS_MODEL_HEADERS": json.dumps(settings.headers),
           "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": str(Path(ROOT) / "backend") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    await progress("AgentDebugX 诊断", 5, {})
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tracelens.diagnosis.agentdebugx_worker", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        result, _ = await process.communicate(json.dumps(payload, ensure_ascii=False).encode())
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode:
        raise ModelError("AgentDebugX 执行失败，请检查模型配置或重试。")
    output = json.loads(result)
    report = present_agentdebugx(trace, request, output, settings.model, settings.prompt_language)
    await progress("AgentDebugX 完成", 100, report)
    return report
