"""Bridge between the workbench trajectory and the AgentDebugX runtime."""

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from time import monotonic

from ..citation_views import locate_quote
from ..config import ROOT
from ..deepagent import obj, span_display_layout, span_kind
from ..llm import ModelError
from ..model_concurrency import model_slot_environment
from ..trajdebug import to_unified
from . import AGENTDEBUGX_COMMIT, AGENTDEBUGX_URL, AGENTDEBUGX_VERSION
from .agentdebugx_prompts import prompt_profile
from .agentdebugx_source import verify_agentdebugx


EVENT_TYPES = {"assistant": "llm.response", "tool": "tool.result", "user": "observation", "system": "observation"}
WORKER_FAILURE = "AgentDebugX 运行器异常退出，请检查服务部署和日志。"
MODEL_FAILURES = {
    "response": "AgentDebugX 未收到可解析的 JSON 诊断结果，请检查模型的返回格式。",
    "length": "AgentDebugX 的模型输出被截断，请检查系统设置中的单次输出上限。",
    "timeout": "AgentDebugX 请求模型服务超时，请检查服务负载和请求超时设置。",
    "connection": "AgentDebugX 无法连接模型服务，请核对接口地址及服务器网络。",
}
HTTP_FAILURES = {
    400: "请检查模型支持的请求参数和上下文长度。",
    401: "模型服务认证失败，请检查 API Key 或自定义认证头。",
    403: "模型服务拒绝访问，请检查模型权限和网关访问规则。",
    404: "模型服务未找到请求路径或模型，请核对接口地址和模型标识。",
    413: "请求超过服务端大小限制，请检查轨迹长度和网关限制。",
    422: "模型服务无法处理请求，请检查模型兼容性和参数。",
    429: "模型服务已限流或额度不足，请检查账户状态。",
}


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
    started = monotonic()
    if not settings.ready:
        raise ModelError("当前分析的模型连接不可用，请检查系统设置后重新分析。")
    verify_agentdebugx()
    request = to_agentdebugx(trace)
    payload = {
        "trajectory": request, "outcome": trace.outcome,
        "settings": {key: getattr(settings, key) for key in
                     ("model", "base_url", "prompt_language", "timeout", "max_retries", "max_tokens")},
    }
    env = {**os.environ, **model_slot_environment(settings), "OPENAI_API_KEY": settings.api_key or "EMPTY",
           "TRACELENS_MODEL_HEADERS": json.dumps(settings.headers),
           "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": str(Path(ROOT) / "backend") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    if os.getenv("DETECTOR_HTTP_TRUST_ENV", "").strip().lower() not in {"1", "true", "yes", "on"}:
        # Match TrajDebug's direct-connection default. httpx honors both spellings.
        env.update(NO_PROXY="*", no_proxy="*")
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
        raise ModelError(WORKER_FAILURE)
    try:
        output = json.loads(result)
    except (UnicodeError, ValueError) as exc:
        raise ModelError(WORKER_FAILURE) from exc
    if failure := output.get("failure"):
        if failure["kind"] == "http":
            status = failure["status"]
            hint = HTTP_FAILURES.get(status, "请检查模型服务和网关状态。")
            raise ModelError(f"AgentDebugX 模型请求失败（HTTP {status}）。{hint}")
        raise ModelError(MODEL_FAILURES.get(failure["kind"], WORKER_FAILURE))
    report = present_agentdebugx(trace, request, output, settings.model, settings.prompt_language)
    report["duration_seconds"] = round(monotonic() - started, 2)
    await progress("AgentDebugX 完成", 100, report)
    return report
