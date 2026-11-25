"""Project persisted span sources into the workbench call viewer."""

from .deepagent import source_bundle, span_display_layout
from .otel_genai import INPUT, OUTPUT, SYSTEM, _attributes


def _deepagent_execution(data):
    bundle = source_bundle(data)
    spans = {span["span_id"]: span for span in bundle["spans"]}
    calls = []
    for node in bundle["nodes"]:
        span = spans[node["span_id"]]
        calls.append({
            **node,
            "title": str(span.get("span_name") or "").strip() or node["title"],
            "parent_id": span.get("parent_id"),
            "input": span.get("span_input"),
            "output": span.get("span_output"),
            "raw": span,
        })
    return {"calls": calls, "message_sources": [
        {**source, "primary": True} for source in bundle["message_sources"]
    ]}


def _otel_execution(data):
    spans = []
    for record in data["otel"]["spans"]:
        raw = record["span"]
        spans.append({
            "span_id": raw["spanId"].lower(),
            "parent_id": (raw.get("parentSpanId") or "").lower() or None,
            "start_time": int(raw["startTimeUnixNano"]),
            "end_time": int(raw["endTimeUnixNano"]),
            "raw": raw,
        })
    calls = []
    for span, start, end, depth in span_display_layout(spans):
        raw = span["raw"]
        attrs = _attributes(raw.get("attributes", []))
        tool = attrs.get("gen_ai.operation.name") == "execute_tool"
        status = raw.get("status")
        reasons = attrs.get("gen_ai.response.finish_reasons")
        calls.append({
            "span_id": span["span_id"],
            "parent_id": span["parent_id"],
            "title": raw.get("name") or span["span_id"],
            "kind": attrs.get("gen_ai.operation.name") or raw.get("kind") or "span",
            # Subtract as integers before converting, preserving sub-microsecond intervals.
            "start_offset_us": start / 1000,
            "end_offset_us": end / 1000,
            "duration_us": (end - start) / 1000,
            "depth": depth,
            "status_code": status.get("code") if isinstance(status, dict) else None,
            "finish_reason": ", ".join(map(str, reasons)) if isinstance(reasons, list) else reasons,
            "input": attrs.get("gen_ai.tool.call.arguments") if tool else attrs.get(INPUT, attrs.get(SYSTEM)),
            "output": attrs.get("gen_ai.tool.call.result") if tool else attrs.get(OUTPUT),
            "raw": raw,
        })
    mappings = []
    for message in data["messages"]:
        refs = message.get("source_refs") or []
        primary = next((ref for ref in refs if ref["attribute"] == "gen_ai.tool.call.result"), None)
        if primary is None and refs:
            primary = refs[0]
        mappings.extend({
            "step": message["step"],
            "span_id": ref["span_id"].lower(),
            "field": ref["attribute"],
            "primary": ref is primary,
        } for ref in refs)
    return {"calls": calls, "message_sources": mappings}


def execution_view(data):
    if data.get("deepagent"):
        return _deepagent_execution(data)
    if data.get("otel"):
        return _otel_execution(data)
    return {"calls": [], "message_sources": []}
