"""Convert DeepAgent's span export into a chronological diagnosis input."""
import json
from collections import defaultdict
from pathlib import Path

from .models import Message, Trajectory


FAILURE_REASONS = {"content_filter", "error", "failed", "ai_security_guard_input", "ai_security_guard_output"}


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


def convert_trace(trace_id, spans, filename, outcome):
    ordered = sorted(spans, key=lambda span: (span["start_time"], span["end_time"], span["span_id"]))
    kinds = {span["span_id"]: span.get("ai_span_kind") or obj(span.get("attributes")).get("gen_ai.span.kind")
             for span in ordered}
    # STEP children are the main Agent's model calls. Unscoped calls can be
    # auxiliary (e.g. title generation) and supply a different system prompt.
    llms = [span for span in ordered if kinds[span["span_id"]] == "LLM"]
    step_llms = [span for span in llms if kinds.get(span.get("parent_id")) == "STEP"]
    agents = [span for span in ordered if kinds[span["span_id"]] == "AGENT"]
    task_span, task = next(((span, value) for span in agents
                           if isinstance(value := obj(span.get("span_input")).get("message"), str) and value.strip()),
                           (None, ""))
    messages, history = [], []
    systems = set()

    def append(message, span, field):
        role, content, name = message
        messages.append(Message(
            step=len(messages), role=role, content=content, name=name,
            source_step=f"{span['span_id']}:{field}",
            judgable=False if role in {"system", "user"} else None,
            compress_role="preserve" if role in {"system", "user"} else "default",
        ))

    last_model_finish = None
    for span in step_llms or llms:
        request = obj(span.get("span_input")).get("messages")
        incoming = []
        for raw in request if isinstance(request, list) else []:
            message = chat_message(raw)
            if message is None:
                continue
            if message[0] == "system":
                if message not in systems:
                    append(message, span, "span_input")
                    systems.add(message)
            else:
                incoming.append(message)
        for message in incoming[overlap(history, incoming):]:
            append(message, span, "span_input")
            history.append(message)

        choices = obj(span.get("span_output")).get("choices")
        choice = next((item for item in choices if isinstance(item, dict)), {}) if isinstance(choices, list) else {}
        reply = chat_message(choice.get("message"))
        if reply:
            append(reply, span, "span_output")
            history.append(reply)
        last_model_finish = choice.get("finish_reason")
        if failed_finish(last_model_finish):
            append(("tool", f"finish_reason: {last_model_finish}", "LLM"), span, "span_output")

    terminal = max(agents, key=lambda span: span["end_time"], default=None)
    terminal_finish = None
    final = ""
    if terminal:
        output = obj(terminal.get("span_output"))
        parts = decoded(output.get("parts"))
        if isinstance(parts, list):
            final = "\n".join(part["text"] for part in parts if isinstance(part, dict)
                              and part.get("type") == "text" and isinstance(part.get("text"), str))
            if final.strip() and (not history or history[-1][:2] != ("assistant", final)):
                append(("assistant", final, ""), terminal, "span_output")
        terminal_finish = obj(output.get("info")).get("finish")
        if not terminal_finish and isinstance(parts, list):
            terminal_finish = next((part.get("reason") for part in reversed(parts) if isinstance(part, dict)
                                    and part.get("type") == "step-finish" and part.get("reason")), None)
        if failed_finish(terminal_finish):
            append(("tool", f"finish: {terminal_finish}", "DeepAgent"), terminal, "span_output")

    if task_span and not any(message.role == "user" for message in messages):
        first_action = next((i for i, message in enumerate(messages) if message.role != "system"), len(messages))
        messages.insert(first_action, Message(step=first_action, role="user", content=task, judgable=False,
                                              compress_role="preserve", source_step=f"{task_span['span_id']}:span_input"))
        for index, message in enumerate(messages[first_action:], first_action):
            message.step = index

    observed_finish = terminal_finish or (last_model_finish if not final.strip() else None)
    if outcome == "unknown" and failed_finish(observed_finish):
        outcome = "failed"

    if not any(message.role in {"assistant", "tool"} for message in messages):
        raise ValueError("DeepAgent 轨迹缺少可诊断的 Agent 输出或模型结果")
    return Trajectory(title=Path(filename).stem, task_id=trace_id, dataset="deepagent", input_format="deepagent",
                      task_description=task or next((m.content for m in messages if m.role == "user"), ""),
                      outcome=outcome, messages=messages, source=filename)


def convert_deepagent(data, filename):
    result = data["result"]
    spans = result.get("spans")
    if not isinstance(spans, list) or not spans:
        raise ValueError("DeepAgent 导出需要非空的 result.spans 数组")
    groups = defaultdict(dict)
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
    outcome = result.get("outcome", data.get("outcome")) if len(groups) == 1 else None
    if outcome not in ("failed", "succeeded"):
        outcome = "unknown"
    return [convert_trace(trace_id, list(group.values()), filename, outcome) for trace_id, group in groups.items()]
