"""Convert DeepAgent's span export into a chronological diagnosis input."""
import json
from collections import defaultdict
from graphlib import CycleError, TopologicalSorter
from heapq import heappop, heappush
from pathlib import Path

from .models import DeepAgentProvenance, Message, Trajectory


FAILURE_REASONS = {"content_filter", "error", "failed", "ai_security_guard_input", "ai_security_guard_output"}
OBSERVATION_ATTRIBUTES = (
    "gen_ai.agent.step.id", "gen_ai.agent.step.round", "gen_ai.agent.step.finish_reason",
    "gen_ai.model_name", "gen_ai.tool.name", "tool.name", "tool.type", "skill.name",
    "http.status_code", "success", "operation", "error.type", "error.message",
)
FRAMEWORK = (
    "Each DeepAgent step records one completed span. Its Input and Output sections describe the request and result. "
    "The parent_span_id links the span to its caller. Calls can overlap, and steps are ordered by completion. "
    "Span status describes execution, while the task outcome describes business success or failure. "
    "Assistant messages in the main conversation are model actions. A diagnostic candidate still needs evidence of an error."
)


def is_deepagent(data):
    if not isinstance(data, dict) or not isinstance(result := data.get("result"), dict):
        return False
    return "spans" in result and ("totalSpans" in result or "currentTime" in data)


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def obj(value):
    value = decoded(value)
    return value if isinstance(value, dict) else {}


def content_text(value):
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def chat_message(value):
    if not isinstance(value, dict) or value.get("role") not in ("system", "developer", "user", "assistant", "tool"):
        return None
    role = "system" if value["role"] == "developer" else value["role"]
    content = content_text(value.get("content"))
    if value.get("tool_calls"):
        content = "\n".join(filter(None, (content, content_text(value["tool_calls"]))))
    call_id = value.get("tool_call_id")
    if role == "tool" and isinstance(call_id, str) and call_id:
        content = f"tool_call_id: {call_id}\n{content}"
    return (role, content, content_text(value.get("name"))) if content else None


def failed_finish(value):
    return isinstance(value, str) and value.lower().replace("-", "_") in FAILURE_REASONS


def payload(value):
    """Keep captured execution payloads separate from HTTP credentials and headers."""
    if isinstance(value, dict):
        return {key: payload(item) for key, item in value.items()
                if key.lower() not in {"headers", "authorization", "cookie", "api_key", "apikey", "access_token", "password", "secret"}}
    if isinstance(value, list):
        return [payload(item) for item in value]
    return value


def present_value(value):
    return value is not None and value != "" and value != [] and value != {}


def span_kind(span, attributes):
    return str(span.get("ai_span_kind") or attributes.get("gen_ai.span.kind") or "SPAN").upper()


def span_title(span, kind):
    return next((value for key in ("span_display_name", "span_name")
                 if isinstance(value := span.get(key), str) and value.strip()), f"DeepAgent {kind}")


def model_choice(output):
    choices = output.get("choices")
    return next((item for item in choices if isinstance(item, dict)), {}) if isinstance(choices, list) else {}


def agent_parts(output):
    parts = decoded(output.get("parts"))
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


def finish_reason(kind, attributes, output):
    finish = attributes.get("gen_ai.agent.step.finish_reason")
    if kind == "LLM":
        return model_choice(output).get("finish_reason") or finish
    if kind == "AGENT":
        return obj(output.get("info")).get("finish") or next(
            (part["reason"] for part in reversed(agent_parts(output)) if part.get("type") == "step-finish" and part.get("reason")),
            finish)
    return finish


def span_message(span, kind, attributes, request, response, *, role, auxiliary, finish, error):
    title = span_title(span, kind)
    details = {"span_id": span["span_id"], "parent_span_id": span.get("parent_id"), "kind": kind, "name": title}
    if kind == "LLM":
        details["scope"] = "auxiliary or unscoped" if auxiliary else "main conversation"
    metadata = {key: attributes[key] for key in OBSERVATION_ATTRIBUTES if present_value(attributes.get(key))}
    http_status = str(metadata.get("http.status_code", ""))
    status_code = span.get("status_code")
    status = str(status_code).lower()
    judgable = (role == "assistant" or failed_finish(finish) or present_value(error) or status in {"1", "2", "error", "failed"}
                or metadata.get("success") is False or (http_status.isdecimal() and len(http_status) == 3 and int(http_status) >= 400)
                or present_value(metadata.get("error.message")) or (kind.startswith("TOOL") and present_value(request))
                or (kind not in {"LLM", "AGENT"} and present_value(response)))
    sections = [("Span", details), ("Input", request), ("Output", response), ("Span status code", status_code),
                ("Finish reason", finish), ("Error", error), ("Attributes", metadata)]
    content = "\n\n".join(f"[{label}]\n{content_text(value)}" for label, value in sections if present_value(value))
    return Message(step=0, role=role, content=content, name="" if role == "assistant" else title,
                   source_step=f"{span['span_id']}:span", judgable=judgable)


def overlap(recorded, incoming):
    """Find the longest repeated prefix of a model's request history."""
    # Requests can omit older context. Match the latest suffix first, then
    # fall back to the shared prefix for a request that truncates the tail.
    prefix = 0
    for old, new in zip(recorded, incoming):
        if old != new:
            break
        prefix += 1
    for size in range(min(len(recorded), len(incoming)), prefix, -1):
        if recorded[-size] == incoming[0] and recorded[-size:] == incoming[:size]:
            return size
    return prefix


def call_scope(span, spans_by_id, kinds):
    parent_id = span.get("parent_id")
    visited = {span["span_id"]}
    in_step = False
    while parent_id in spans_by_id and parent_id not in visited:
        kind = kinds[parent_id]
        if kind == "AGENT":
            return parent_id, in_step
        in_step = in_step or kind == "STEP"
        visited.add(parent_id)
        parent_id = spans_by_id[parent_id].get("parent_id")
    return None, in_step


def span_timeline(spans_by_id):
    """Order ready events by timestamp after honoring known span dependencies."""
    graph = TopologicalSorter()
    for span_id, span in spans_by_id.items():
        start = span_id, "start"
        end = span_id, "end"
        graph.add(end, start)
        parent_id = span.get("parent_id")
        if parent_id not in spans_by_id:
            continue
        graph.add(start, (parent_id, "start"))
        if span["end_time"] == spans_by_id[parent_id]["end_time"]:
            graph.add((parent_id, "end"), end)
    try:
        graph.prepare()
    except CycleError as exc:
        raise ValueError("DeepAgent span 的父子关系包含循环") from exc

    ready = []

    def enqueue(available_at=None):
        for span_id, phase in graph.get_ready():
            span = spans_by_id[span_id]
            time = span[f"{phase}_time"]
            if available_at is not None:
                time = max(time, available_at)
            # Unrelated completions are already observable at the next call's start.
            heappush(ready, (time, phase == "start", span["end_time"], span_id, phase))

    timeline = {}
    enqueue()
    while ready:
        time, _, _, span_id, phase = heappop(ready)
        event = span_id, phase
        timeline[event] = len(timeline)
        graph.done(event)
        enqueue(time)
    return timeline


def convert_trace(trace_id, spans, filename, outcome):
    spans_by_id = {span["span_id"]: span for span in spans}
    timeline = span_timeline(spans_by_id)
    ordered = [spans_by_id[span_id] for span_id, phase in timeline if phase == "start"]
    attributes = {span["span_id"]: obj(span.get("attributes")) for span in ordered}
    kinds = {span["span_id"]: span_kind(span, attributes[span["span_id"]]) for span in ordered}
    llm_scopes = {span_id: call_scope(span, spans_by_id, kinds) for span_id, span in spans_by_id.items()
                  if kinds[span_id] == "LLM"}
    agents_with_steps = {owner for owner, in_step in llm_scopes.values() if in_step}
    agents = [span for span in ordered if kinds[span["span_id"]] == "AGENT"]
    task = next((message for span in agents
                 if isinstance(message := obj(span.get("span_input")).get("message"), str) and message.strip()), "")
    terminal = max(agents, key=lambda span: timeline[span["span_id"], "end"], default=None)

    histories = defaultdict(list)
    systems_by_agent = defaultdict(set)
    replies_by_agent = defaultdict(list)
    finishes_by_agent = {}
    completed = {}
    deferred_agents = {}
    terminal_finish = None
    terminal_has_answer = False
    for span in ordered:
        span_id = span["span_id"]
        kind = kinds[span_id]
        attrs = attributes[span_id]
        raw_input = decoded(span.get("span_input"))
        raw_output = decoded(span.get("span_output"))
        output = obj(raw_output)
        request, response = payload(raw_input), payload(raw_output)
        finish = finish_reason(kind, attrs, output)
        error = output.get("error")
        owner, in_step = llm_scopes.get(span_id, (None, False))
        auxiliary = kind == "LLM" and owner in agents_with_steps and not in_step
        role = "tool"
        if kind == "LLM" and not auxiliary:
            history, systems = histories[owner], systems_by_agent[owner]
            request_messages = obj(request).get("messages")
            if isinstance(request_messages, list):
                incoming, new_systems = [], []
                for item in request_messages:
                    message = chat_message(item)
                    if message is None:
                        continue
                    if message[0] != "system":
                        incoming.append(message)
                    elif message not in systems:
                        new_systems.append(message)
                        systems.add(message)
                start = timeline[span_id, "start"]
                context = [message for position, message in sorted(history, key=lambda entry: entry[0]) if position < start]
                additions = incoming[overlap(context, incoming):]
                history.extend((start, message) for message in additions)
                request = "\n".join(f"{message_role}: {content}" for message_role, content, _ in new_systems + additions)
                if not request and incoming:
                    request = "Previously recorded conversation history."
                if not task:
                    task = next((content for message_role, content, _ in incoming if message_role == "user"), "")
            choice = model_choice(output)
            if choice:
                response = payload(choice.get("message"))
            reply = chat_message(response)
            end = timeline[span_id, "end"]
            if reply and reply[0] == "assistant":
                role = "assistant"
                response = reply[1]
                history.append((end, reply))
                replies_by_agent[owner].append((end, reply[1]))
            latest = finishes_by_agent.get(owner)
            if latest is None or end >= latest[0]:
                finishes_by_agent[owner] = (end, finish)
        elif kind == "AGENT":
            request = payload(obj(raw_input).get("message"))
            parts = agent_parts(output)
            answer = "\n".join(part["text"] for part in parts if part.get("type") == "text" and isinstance(part.get("text"), str))
            output_events = [payload(part) for part in parts if part.get("type") not in ("text", "step-start", "step-finish")]
            error = obj(output.get("info")).get("error") or error
            if span is terminal:
                terminal_finish = finish
                terminal_has_answer = bool(answer.strip())
            deferred_agents[span_id] = request, answer, output_events, finish, payload(error)
            continue
        completed[span_id] = span_message(span, kind, attrs, request, response, role=role,
                                          auxiliary=auxiliary, finish=finish, error=payload(error))

    for span_id, (request, answer, events, finish, error) in deferred_agents.items():
        # Agent and model spans can both report the terminal answer.
        previous = max((item for item in replies_by_agent[span_id] if item[0] < timeline[span_id, "end"]),
                       key=lambda item: item[0], default=None)
        if previous and previous[1] == answer:
            answer = ""
        role = "assistant" if answer.strip() else "tool"
        response = "\n".join(filter(None, (answer, content_text(events) if events else "")))
        completed[span_id] = span_message(spans_by_id[span_id], "AGENT", attributes[span_id], request, response,
                                          role=role, auxiliary=False, finish=finish, error=error)

    messages = [completed[span_id] for span_id, phase in timeline if phase == "end"]
    for index, message in enumerate(messages):
        message.step = index

    fallback_finish = finishes_by_agent.get(None, (0, None))
    last_model_finish = finishes_by_agent.get(terminal["span_id"], fallback_finish) if terminal else fallback_finish
    observed_finish = terminal_finish or (last_model_finish[1] if not terminal_has_answer else None)
    if outcome == "unknown" and failed_finish(observed_finish):
        outcome = "failed"
    return Trajectory(title=Path(filename).stem, task_id=trace_id, dataset="deepagent", input_format="deepagent",
                      task_description=task, outcome=outcome, messages=messages, source=filename,
                      agent_framework_description=FRAMEWORK)


def convert_deepagent(data, filename):
    result = data["result"]
    spans = result.get("spans")
    if not isinstance(spans, list) or not spans:
        raise ValueError("DeepAgent 导出需要非空的 result.spans 数组")
    groups = defaultdict(dict)
    originals = defaultdict(list)
    for span in spans:
        if (not isinstance(span, dict)
                or not all(isinstance(span.get(key), str) and span[key].strip() for key in ("trace_id", "span_id"))
                or not all(type(span.get(key)) is int for key in ("start_time", "end_time"))
                or span.get("parent_id") is not None and not isinstance(span["parent_id"], str)):
            raise ValueError("DeepAgent span 的 ID、父 ID 或时间字段无效")
        group = groups[span["trace_id"]]
        if span["span_id"] in group and group[span["span_id"]] != span:
            raise ValueError("DeepAgent 导出包含冲突的重复 span")
        group[span["span_id"]] = span
        originals[span["trace_id"]].append(span)
    outcome = result.get("outcome", data.get("outcome")) if len(groups) == 1 else None
    if outcome not in ("failed", "succeeded"):
        outcome = "unknown"
    metadata = {**data, "result": {key: value for key, value in result.items() if key != "spans"}}
    traces = []
    for trace_id, group in groups.items():
        trace = convert_trace(trace_id, list(group.values()), filename, outcome)
        trace.deepagent = DeepAgentProvenance(trace_id=trace_id, spans=originals[trace_id], export_metadata=metadata)
        traces.append(trace)
    return traces


def trace_with_source_summary(trace):
    source = trace["data"].get("deepagent")
    if not source:
        return trace
    summary = {"trace_id": source["trace_id"], "span_count": len({span["span_id"] for span in source["spans"]})}
    return {**trace, "data": {**trace["data"], "deepagent": summary}}


def span_display_layout(spans):
    by_id = {span["span_id"]: span for span in spans}
    try:
        ordered_ids = [span_id for span_id, event in span_timeline(by_id) if event == "start"]
    except ValueError:
        # Historical imports can contain cycles accepted by older converters.
        ordered_ids = sorted(by_id, key=lambda span_id: (by_id[span_id]["start_time"], span_id))
    origin = min((span["start_time"] for span in by_id.values()), default=0)
    depths = {}
    for span_id in ordered_ids:
        span = by_id[span_id]
        depth = depths.get(span.get("parent_id"), -1) + 1
        depths[span_id] = depth
        yield span, span["start_time"] - origin, span["end_time"] - origin, depth


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


def source_nodes(spans):
    nodes = []
    for span, start_offset, end_offset, depth in span_display_layout(spans):
        attrs = obj(span.get("attributes"))
        kind = span_kind(span, attrs)
        duration = span.get("duration")
        if type(duration) not in (int, float) or not 0 <= duration < 2**64:
            duration = span["end_time"] - span["start_time"]
        status_code = span.get("status_code")
        finish = finish_reason(kind, attrs, obj(span.get("span_output")))
        nodes.append({"span_id": span["span_id"], "title": span_title(span, kind), "kind": kind,
                      "duration_us": duration if 0 <= duration < 2**64 else None,
                      "start_offset_us": start_offset if start_offset < 2**64 else None,
                      "end_offset_us": end_offset if start_offset <= end_offset < 2**64 else None,
                      "depth": depth,
                      "status_code": status_code if type(status_code) in (str, int) else None,
                      "finish_reason": finish if isinstance(finish, str) else None})
    return nodes


def source_bundle(data):
    source = data["deepagent"]
    mappings = []
    for message in data["messages"]:
        span_id, separator, field = message["source_step"].rpartition(":")
        if separator and field in {"span_start", "span", "span_input", "span_output"}:
            mappings.append({"step": message["step"], "span_id": span_id, "field": field})
    return {**source, "nodes": source_nodes(source["spans"]), "message_sources": mappings}
