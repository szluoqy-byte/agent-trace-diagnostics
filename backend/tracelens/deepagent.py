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
    "DeepAgent span start events show the call and available inputs. Completion events show the recorded result. "
    "The parent_span_id links each call to its caller. Time intervals may overlap. "
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


def span_details(span, kind, event):
    return {
        "span_id": span["span_id"], "parent_span_id": span.get("parent_id"), "kind": kind,
        "name": span.get("span_display_name") or span.get("span_name"),
        "event": event, "start_time": span["start_time"],
    }


def span_message(details, kind):
    content = json.dumps({key: value for key, value in details.items() if present_value(value) or key == "parent_span_id"},
                         ensure_ascii=False, separators=(",", ":"))
    return "tool", content, f"DeepAgent {kind}"


def start_span(span, kind, request, *, auxiliary):
    details = span_details(span, kind, "start")
    if kind == "LLM":
        details["scope"] = "auxiliary or unscoped" if auxiliary else "main conversation"
        if auxiliary:
            details["input_messages"] = payload(obj(request).get("messages"))
    elif kind == "AGENT":
        details["input_message"] = payload(obj(request).get("message"))
    else:
        details["input"] = payload(request)
    judgable = kind.startswith("TOOL") and present_value(details.get("input"))
    return span_message(details, kind), judgable


def observe_span(span, kind, attributes, output, *, auxiliary, finish, error):
    details = {**span_details(span, kind, "end"), "end_time": span["end_time"], "status_code": span.get("status_code")}
    for key in OBSERVATION_ATTRIBUTES:
        if present_value(value := attributes.get(key)):
            details[key] = value
    if present_value(finish):
        details["finish_reason"] = finish
    if present_value(error):
        details["error"] = payload(error)

    if kind == "LLM":
        details["scope"] = "auxiliary or unscoped" if auxiliary else "main conversation"
        if auxiliary:
            details["output_choices"] = payload(obj(output).get("choices"))
        if not obj(output).get("choices"):
            details["output"] = payload(output)
    elif kind == "AGENT":
        parts = decoded(obj(output).get("parts"))
        if isinstance(parts, list):
            details["output_events"] = [payload(part) for part in parts if isinstance(part, dict)
                                        and part.get("type") not in ("text", "step-start", "step-finish")]
    else:
        details["output"] = payload(output)

    status = str(details["status_code"]).lower()
    http_status = str(details.get("http.status_code", ""))
    judgable = (failed_finish(finish) or present_value(error) or status in {"1", "2", "error", "failed"}
                or details.get("success") is False or (http_status.isdecimal() and len(http_status) == 3 and int(http_status) >= 400)
                or present_value(details.get("error.message")) or present_value(details.get("output")))
    return span_message(details, kind), judgable


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
    kinds = {
        span["span_id"]: str(span.get("ai_span_kind") or attributes[span["span_id"]].get("gen_ai.span.kind") or "SPAN").upper()
        for span in ordered
    }
    llm_scopes = {span_id: call_scope(span, spans_by_id, kinds) for span_id, span in spans_by_id.items()
                  if kinds[span_id] == "LLM"}
    agents_with_steps = {owner for owner, in_step in llm_scopes.values() if in_step}
    agents = [span for span in ordered if kinds[span["span_id"]] == "AGENT"]
    task_span, task = next(((span, message) for span in agents
                           if isinstance(message := obj(span.get("span_input")).get("message"), str) and message.strip()),
                           (None, ""))
    terminal = max(agents, key=lambda span: timeline[span["span_id"], "end"], default=None)

    events = []
    histories = defaultdict(list)
    systems_by_agent = defaultdict(set)
    replies_by_agent = defaultdict(list)
    agent_answers = []

    def record(message, span, field, *, judgable=None):
        role, content, name = message
        preserve = role in {"system", "user"}
        phase = "start" if field in {"span_start", "span_input"} else "end"
        step = Message(step=0, role=role, content=content, name=name,
                       source_step=f"{span['span_id']}:{field}", judgable=False if preserve else judgable,
                       compress_role="preserve" if preserve else "default")
        events.append((timeline[span["span_id"], phase], step))

    finishes_by_agent = {}
    terminal_finish = None
    terminal_has_answer = False
    for span in ordered:
        span_id = span["span_id"]
        kind = kinds[span_id]
        request = decoded(span.get("span_input"))
        response = decoded(span.get("span_output"))
        output = obj(response)
        user_input = obj(request)
        finish = attributes[span_id].get("gen_ai.agent.step.finish_reason")
        error = output.get("error")
        owner, in_step = llm_scopes.get(span_id, (None, False))
        # Direct calls alongside STEP-scoped calls can be auxiliary work.
        auxiliary = kind == "LLM" and owner in agents_with_steps and not in_step
        opening, judgable = start_span(span, kind, request, auxiliary=auxiliary)
        record(opening, span, "span_start", judgable=judgable)

        if kind == "LLM":
            choices = output.get("choices")
            choice = next((item for item in choices if isinstance(item, dict)), {}) if isinstance(choices, list) else {}
            finish = choice.get("finish_reason") or finish
            if not auxiliary:
                history, systems = histories[owner], systems_by_agent[owner]
                request_messages = user_input.get("messages")
                incoming = []
                for item in request_messages if isinstance(request_messages, list) else []:
                    message = chat_message(item)
                    if message is None:
                        continue
                    if message[0] != "system":
                        incoming.append(message)
                    elif message not in systems:
                        record(message, span, "span_input")
                        systems.add(message)
                start = timeline[span_id, "start"]
                end = timeline[span_id, "end"]
                context = [message for position, message in sorted(history, key=lambda entry: entry[0]) if position < start]
                for message in incoming[overlap(context, incoming):]:
                    record(message, span, "span_input")
                    history.append((start, message))
                reply = chat_message(choice.get("message"))
                if reply:
                    record(reply, span, "span_output")
                    history.append((end, reply))
                    replies_by_agent[owner].append((end, reply[1]))
                latest = finishes_by_agent.get(owner)
                if latest is None or end >= latest[0]:
                    finishes_by_agent[owner] = (end, finish)
        elif kind == "AGENT":
            info = obj(output.get("info"))
            parts = decoded(output.get("parts"))
            parts = parts if isinstance(parts, list) else []
            final = "\n".join(part["text"] for part in parts if isinstance(part, dict)
                              and part.get("type") == "text" and isinstance(part.get("text"), str))
            if final.strip():
                agent_answers.append((span, final))
            finish = info.get("finish") or next((part["reason"] for part in reversed(parts) if isinstance(part, dict)
                                                and part.get("type") == "step-finish" and part.get("reason")), finish)
            error = info.get("error") or error
            if span is terminal:
                terminal_finish = finish
                terminal_has_answer = bool(final.strip())

        observation, judgable = observe_span(span, kind, attributes[span_id], response,
                                             auxiliary=auxiliary, finish=finish, error=error)
        record(observation, span, "span", judgable=judgable)

    for span, answer in agent_answers:
        # Both the Agent and its model can report the same terminal answer.
        previous = max((item for item in replies_by_agent[span["span_id"]] if item[0] < timeline[span["span_id"], "end"]),
                       key=lambda item: item[0], default=None)
        if not previous or previous[1] != answer:
            record(("assistant", answer, ""), span, "span_output")

    if task_span and not any(message.role == "user" for _, message in events):
        record(("user", task, ""), task_span, "span_input")
    messages = [message for _, message in sorted(events, key=lambda event: event[0])]
    for index, message in enumerate(messages):
        message.step = index

    fallback_finish = finishes_by_agent.get(None, (0, None))
    last_model_finish = finishes_by_agent.get(terminal["span_id"], fallback_finish) if terminal else fallback_finish
    observed_finish = terminal_finish or (last_model_finish[1] if not terminal_has_answer else None)
    if outcome == "unknown" and failed_finish(observed_finish):
        outcome = "failed"
    return Trajectory(title=Path(filename).stem, task_id=trace_id, dataset="deepagent", input_format="deepagent",
                      task_description=task or next((m.content for m in messages if m.role == "user"), ""),
                      outcome=outcome, messages=messages, source=filename, agent_framework_description=FRAMEWORK)


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
    summary = {"trace_id": source["trace_id"], "span_count": len(source["spans"])}
    return {**trace, "data": {**trace["data"], "deepagent": summary}}


def source_bundle(data):
    source = data["deepagent"]
    mappings = []
    for message in data["messages"]:
        span_id, separator, field = message["source_step"].rpartition(":")
        if separator and field in {"span_start", "span", "span_input", "span_output"}:
            mappings.append({"step": message["step"], "span_id": span_id, "field": field})
    return {**source, "message_sources": mappings}
