"""OTLP/JSON GenAI ingestion. The upstream detector still receives unified JSON.

Only serial, consistent message histories can be represented by the upstream
linear schema. Raw telemetry is kept for inspection, never added to prompts.
"""
from collections import defaultdict
from dataclasses import dataclass
import json
import re

from .agent_identity import AgentOwnership
from .models import Message, OTelProvenance, OTelSource, RuntimeInput, Trajectory


INPUT = "gen_ai.input.messages"
OUTPUT = "gen_ai.output.messages"
SYSTEM = "gen_ai.system_instructions"
FRAMEWORK = (
    "A serial agent trajectory reconstructed from captured OpenTelemetry GenAI messages. "
    "Assistant messages are model decisions and tool-call requests; tool messages are "
    "results visible in a subsequent model input. Repeated input history is represented "
    "once. Span status is transport telemetry, not the task outcome."
)


def is_otlp(data):
    # An existing message log may carry telemetry as an extra attachment.
    # Its explicit message format retains priority for backwards compatibility.
    return (isinstance(data, dict) and "resourceSpans" in data
            and not any(key in data for key in ("messages", "traj", "events", "trajectory")))


def _array(value, where):
    if not isinstance(value, list):
        raise ValueError(f"OTel {where} 必须是数组")
    return value


def _object(value, where):
    if not isinstance(value, dict):
        raise ValueError(f"OTel {where} 必须是对象")
    return value


def _attributes(entries):
    result = {}
    for entry in _array(entries, "attributes"):
        _object(entry, "attribute")
        key = entry.get("key")
        if not isinstance(key, str) or not key or key in result:
            raise ValueError("OTel attribute key 必须非空且不能重复")
        result[key] = _value(entry.get("value", {}))
    return result


def _value(value):
    _object(value, "AnyValue")
    if not value:
        return None
    if len(value) != 1:
        raise ValueError("OTel AnyValue 必须只包含一种类型")
    key, item = next(iter(value.items()))
    if key == "arrayValue":
        return [_value(v) for v in _array(_object(item, key).get("values", []), key)]
    if key == "kvlistValue":
        return _attributes(_object(item, key).get("values", []))
    if key in {"stringValue", "bytesValue"} and isinstance(item, str):
        return item
    if key == "boolValue" and type(item) is bool:
        return item
    if key == "intValue" and not isinstance(item, bool) and re.fullmatch(r"-?\d+", str(item)):
        return int(item)
    if key == "doubleValue" and type(item) in {int, float}:
        return item
    raise ValueError(f"OTel AnyValue 类型或值不合法：{key}")


def _structured(value, field):
    # Older instrumentations encode the structured attribute as a JSON string.
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"OTel {field} 不是有效的 JSON 内容") from exc
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parts(parts):
    text = []
    for part in _array(parts, "message.parts"):
        _object(part, "message part")
        kind = part.get("type")
        if kind == "text" and isinstance(part.get("content"), str):
            text.append(part["content"])
        elif kind == "tool_call" and isinstance(part.get("name"), str):
            text.append(_json({k: part.get(k) for k in ("type", "id", "name", "arguments")}))
        elif kind == "tool_call_response" and "response" in part:
            text.append(_json({k: part.get(k) for k in ("type", "id", "response")}))
        else:
            # A text-only detector must not invent descriptions of media/opaque state.
            raise ValueError(f"OTel 暂不支持消息部分 {kind!r}，请提供文本及工具调用消息")
    return "\n".join(text)


def _messages(value, field):
    result = []
    for raw in _array(_structured(value, field), field):
        _object(raw, field + " message")
        role = raw.get("role")
        if role not in {"user", "system", "assistant", "tool"}:
            raise ValueError(f"OTel 消息 role 不合法：{role!r}")
        if "parts" in raw:
            content = _parts(raw["parts"])
        elif isinstance(raw.get("content"), str):
            content = raw["content"]
        else:
            raise ValueError("OTel 消息需要 parts 数组或文本 content")
        result.append({"role": role, "content": content, "name": str(raw.get("name") or "")})
    return result


def _identity(message):
    # Match a history position, never globally remove repeated text.
    return message["role"], message["content"]


@dataclass
class Span:
    raw: dict
    resource: dict
    scope: dict
    location: str
    attrs: dict
    start: int
    end: int

    @property
    def id(self):
        return self.raw["spanId"].lower()

    @property
    def trace_id(self):
        return self.raw["traceId"].lower()

    def ref(self, attribute, index=None):
        return OTelSource(trace_id=self.trace_id, span_id=self.id, attribute=attribute, message_index=index)


def _timestamp(value):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)) or not 0 < int(value) < 2**64:
        raise ValueError("OTel span 需要有效的纳秒 startTimeUnixNano/endTimeUnixNano")
    return int(value)


def _read_spans(envelopes):
    groups = defaultdict(dict)
    for doc, envelope in enumerate(envelopes):
        for ri, resource in enumerate(_array(envelope["resourceSpans"], "resourceSpans")):
            _object(resource, "resourceSpans item")
            for si, scope in enumerate(_array(resource.get("scopeSpans", []), "scopeSpans")):
                _object(scope, "scopeSpans item")
                for pi, raw in enumerate(_array(scope.get("spans", []), "spans")):
                    _object(raw, "span")
                    for key, width in (("traceId", 32), ("spanId", 16)):
                        identifier = raw.get(key)
                        if not isinstance(identifier, str) or not re.fullmatch(rf"[a-fA-F0-9]{{{width}}}", identifier) or int(identifier, 16) == 0:
                            raise ValueError(f"OTel {key} 必须为非零的 {width} 位十六进制字符串")
                    start, end = _timestamp(raw.get("startTimeUnixNano")), _timestamp(raw.get("endTimeUnixNano"))
                    if end < start:
                        raise ValueError("OTel span 结束时间早于开始时间")
                    parent = raw.get("parentSpanId", "")
                    if not isinstance(parent, str) or (parent and not re.fullmatch(r"[a-fA-F0-9]{16}", parent)):
                        raise ValueError("OTel parentSpanId 必须是 16 位十六进制字符串")
                    span = Span(raw, _object(resource.get("resource", {}), "resource"), _object(scope.get("scope", {}), "scope"),
                                f"document[{doc}].resourceSpans[{ri}].scopeSpans[{si}].spans[{pi}]",
                                _attributes(raw.get("attributes", [])), start, end)
                    previous = groups[span.trace_id].get(span.id)
                    if previous and previous.raw != raw:
                        raise ValueError(f"OTel spanId {span.id} 存在冲突的重复记录")
                    groups[span.trace_id][span.id] = previous or span
                    if len(groups) > 50 or len(groups[span.trace_id]) > 20000:
                        raise ValueError("OTel 单次最多 50 条 trace，每条最多 20000 个 span")
    return groups


def _leaf_calls(spans):
    candidates = {s.id: s for s in spans.values() if INPUT in s.attrs or OUTPUT in s.attrs}
    ancestors = set()
    for span in candidates.values():
        visited = {span.id}
        parent = span.raw.get("parentSpanId", "").lower()
        while parent in spans:
            if parent in visited:
                raise ValueError("OTel span 父子关系存在循环")
            visited.add(parent)
            ancestors.add(parent)
            parent = spans[parent].raw.get("parentSpanId", "").lower()
    return sorted((s for key, s in candidates.items() if key not in ancestors), key=lambda s: (s.start, s.id))


def _convert_trace(spans, filename):
    calls = _leaf_calls(spans)
    if not calls:
        raise ValueError("OTel GenAI 未采集消息内容：需要 gen_ai.input.messages / gen_ai.output.messages；仅有耗时和 token 无法诊断")
    for previous, current in zip(calls, calls[1:]):
        if current.start < previous.end or current.start == previous.start:
            raise ValueError("OTel 包含并发 GenAI 分支，无法无损转换为官方线性轨迹；请导出单条串行 Agent 会话")
    messages = []
    warnings = []
    runtime_inputs = []

    def append(item, span, field, index):
        if len(messages) >= 20000:
            raise ValueError("OTel 转换后消息数超过每条轨迹 20000 条上限")
        ref = span.ref(field, index)
        messages.append(Message(step=len(messages), **item,
                                source_step=f"{span.trace_id}/{span.id}/{field}/{index}", source_refs=[ref],
                                judgable=item["role"] == "assistant",
                                compress_role="compress" if item["role"] == "tool" else "default"))

    for call in calls:
        if INPUT not in call.attrs:
            raise ValueError("OTel GenAI 缺少输入消息，无法验证历史可见性；请启用输入和输出内容采集")
        inputs = _messages(call.attrs[INPUT], INPUT)
        if not inputs:
            raise ValueError("OTel GenAI 输入消息为空，无法还原任务上下文")
        system = call.attrs.get(SYSTEM)
        instructions = []
        if system is not None:
            instructions = [{"role": "system", "name": "", "content": _parts(_structured(system, SYSTEM))}]
        duplicate_system = bool(instructions and inputs[0]["role"] == "system" and inputs[0]["content"] == instructions[0]["content"])
        snapshot = ([] if duplicate_system else instructions) + inputs
        refs = ([] if duplicate_system else [(SYSTEM, None)] * len(instructions)) + [(INPUT, i) for i in range(len(inputs))]
        history = [_identity(m.model_dump()) for m in messages]
        incoming = [_identity(m) for m in snapshot]
        # Accept full growing snapshots. A truncated/changed history would falsely
        # expose discarded context to the detector, so reject instead of guessing.
        if history and incoming[:len(history)] != history:
            raise ValueError(f"OTel span {call.id} 的输入历史与前序消息不一致（可能截断、改写或跨会话）；请导出完整串行消息历史")
        for i in range(min(len(history), len(snapshot))):
            ref = call.ref(*refs[i])
            if ref not in messages[i].source_refs:
                messages[i].source_refs.append(ref)
        for i in range(len(history), len(snapshot)):
            append(snapshot[i], call, *refs[i])
        if duplicate_system:
            ref = call.ref(SYSTEM)
            if ref not in messages[0].source_refs:
                messages[0].source_refs.append(ref)
        outputs = _messages(call.attrs.get(OUTPUT, []), OUTPUT)
        if len(outputs) > 1:
            raise ValueError("OTel GenAI 含多个输出候选，无法确定实际采用的分支；请导出已选中的一个候选")
        if outputs and "gen_ai.tool.definitions" in call.attrs:
            definitions = _structured(call.attrs["gen_ai.tool.definitions"], "gen_ai.tool.definitions")
            runtime_inputs.append(RuntimeInput(
                source_id=f"otel-tools-{call.id}", kind="tool_schema", content=_json(definitions),
                provenance=f"OTel {call.trace_id}/{call.id} gen_ai.tool.definitions",
                available_from=len(messages), visibility="agent_visible"))
        for i, output in enumerate(outputs):
            if output["role"] != "assistant":
                raise ValueError("OTel GenAI 输出消息必须是 assistant")
            output["name"] = output["name"] or str(call.attrs.get("gen_ai.agent.name") or "")
            append(output, call, OUTPUT, i)
        if not outputs:
            warnings.append(f"span {call.id} 未采集输出，仅保留已记录的输入；未合成错误回答。")

    # Tool execution is evidence, not proof that the agent observed its result.
    # Only results already present in a model input may gain a tool-span reference.
    tool_sources = defaultdict(list)
    for span in spans.values():
        if span.attrs.get("gen_ai.operation.name") != "execute_tool":
            continue
        call_id = span.attrs.get("gen_ai.tool.call.id")
        if not call_id or "gen_ai.tool.call.result" not in span.attrs:
            continue
        matched = False
        for message in messages:
            if message.role != "tool":
                continue
            source = message.source_refs[0]
            observed_at = spans[source.span_id].start
            if span.end > observed_at:
                continue
            if source.attribute != INPUT:
                continue
            raw_inputs = _structured(spans[source.span_id].attrs[INPUT], INPUT)
            raw_message = raw_inputs[source.message_index]
            result = span.attrs["gen_ai.tool.call.result"]
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    pass
            for part in raw_message.get("parts", []):
                if (part.get("type") == "tool_call_response" and part.get("id") == call_id
                        and part.get("response") == result):
                    ref = span.ref("gen_ai.tool.call.result")
                    if ref not in tool_sources[message.step]:
                        tool_sources[message.step].append(ref)
                    matched = True
        if not matched:
            warnings.append(f"工具 span {span.id} 的结果未在已采集输入中确认可见，仅保留原始 span。")
    for step, refs in tool_sources.items():
        if len(refs) == 1:
            messages[step].source_refs.extend(refs)
        else:
            warnings.append(f"消息 #{step} 匹配多个工具 span，未选择推测来源；以模型输入快照为准。")
    if len(calls) < sum(INPUT in s.attrs or OUTPUT in s.attrs for s in spans.values()):
        warnings.append("外层 Agent span 与内层调用重复记录消息，已采用最内层调用；外层 span 保留供查阅。")
    trace_id = calls[0].trace_id
    provenance = OTelProvenance(trace_id=trace_id, warnings=warnings, spans=[
        {"span": s.raw, "resource": s.resource, "scope": s.scope, "location": s.location}
        for s in sorted(spans.values(), key=lambda s: (s.start, s.id))])
    task = next((m.content for m in messages if m.role == "user"), "")
    return Trajectory(title=f"{filename.rsplit('.', 1)[0]} · {trace_id[:8]}", task_id=trace_id,
                      dataset="otel_genai", task_description=task, messages=messages, source=filename,
                      agent_framework_description=FRAMEWORK, runtime_inputs=runtime_inputs, otel=provenance,
                      agent_identity=AgentOwnership({'otel': {'spans': provenance.spans}}).entry())


def convert_envelopes(envelopes, filename):
    """One trajectory per traceId, joining OTLP JSONL batches within this file."""
    traces = []
    for spans in _read_spans(envelopes).values():
        if not any(any(k.startswith("gen_ai.") for k in s.attrs) for s in spans.values()):
            continue
        traces.append(_convert_trace(spans, filename))
    if not traces:
        raise ValueError("OTel 文件没有包含已采集消息的 GenAI trace")
    return traces
