import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tracelens.config import Settings
from tracelens.demo import demo_report
from tracelens.main import create_app
from tracelens.models import Trajectory
from tracelens.normalize import fingerprint, parse_upload
from tracelens.trajdebug import to_unified, verify_upstream


@pytest.fixture
def envelope():
    return json.loads((Path(__file__).parents[2] / "examples/otel-genai.json").read_text())


def spans(envelope):
    return envelope["resourceSpans"][0]["scopeSpans"][0]["spans"]


def attribute(span, key):
    return next(item for item in span["attributes"] if item["key"] == key)


def set_attribute(span, key, value):
    target = next((item for item in span["attributes"] if item["key"] == key), None)
    if target is None:
        target = {"key": key}
        span["attributes"].append(target)
    target["value"] = {"stringValue": json.dumps(value)}


def parsed(envelope):
    return parse_upload(json.dumps(envelope).encode(), "fixture.json")[0]


def test_structured_anyvalue_history_sources_and_official_contract(envelope):
    trace = parsed(envelope)
    assert trace.dataset == "otel_genai" and trace.outcome == "unknown"
    assert [m.role for m in trace.messages] == ["user", "assistant", "tool", "assistant"]
    assert [m.step for m in trace.messages] == [0, 1, 2, 3]
    assert len(trace.messages[0].source_refs) == 2
    assert trace.messages[1].name == "refund-assistant"
    assert len(trace.messages[2].source_refs) == 2
    assert trace.messages[2].source_refs[-1].attribute == "gen_ai.tool.call.result"
    assert trace.messages[2].judgable is False
    assert trace.otel.spans[0]["span"] == spans(envelope)[0]
    trace.outcome = "failed"
    unified = to_unified(trace)  # Executes the unmodified official validator.
    assert unified["metadata"]["reward"] == 0
    assert unified["metadata"]["annotation"] == {}
    assert all("source_refs" not in m and "source_step" not in m for m in unified["messages"])
    assert trace.task_id not in json.dumps(unified["messages"])
    verify_upstream()


def test_json_string_messages_repeated_text_and_original_content(envelope):
    call = spans(envelope)[0]
    identical = {"role": "user", "parts": [{"type": "text", "content": "repeat"}]}
    set_attribute(call, "gen_ai.input.messages", [identical, identical])
    set_attribute(call, "gen_ai.output.messages", [{"role": "assistant", "content": "repeat"}])
    spans(envelope)[:] = [call]
    trace = parsed(envelope)
    assert [m.content for m in trace.messages] == ["repeat", "repeat", "repeat"]


def test_jsonl_groups_shuffled_batches_multiple_traces_and_duplicate_exports(envelope):
    first, second = copy.deepcopy(envelope), copy.deepcopy(envelope)
    spans(first)[:] = [spans(envelope)[2]]
    spans(second)[:] = spans(envelope)[:2]
    other = copy.deepcopy(envelope)
    for span in spans(other):
        span["traceId"] = "a" * 32
    content = "\n".join(json.dumps(v) for v in (first, second, second, other))
    traces = parse_upload(content.encode(), "spans.jsonl")
    assert len(traces) == 2
    assert all(len(t.messages) == 4 and len(t.otel.spans) == 3 for t in traces)


def test_no_unobserved_tool_result_or_transport_status_promoted_to_input(envelope):
    spans(envelope)[0]["status"] = {"code": 2, "message": "RAW_TELEMETRY_SECRET"}
    spans(envelope)[:] = spans(envelope)[:2]
    trace = parsed(envelope)
    assert len(trace.messages) == 2 and trace.outcome == "unknown"
    trace.outcome = "failed"
    serialized = json.dumps(to_unified(trace))
    assert '"status":"refunded"' not in serialized
    assert "RAW_TELEMETRY_SECRET" not in serialized
    assert "RAW_TELEMETRY_SECRET" in trace.model_dump_json()


def test_system_instruction_and_tool_schema_visibility(envelope):
    call = spans(envelope)[0]
    spans(envelope)[:] = [call]
    set_attribute(call, "gen_ai.system_instructions", [{"type": "text", "content": "visible policy"}])
    set_attribute(call, "gen_ai.tool.definitions", [{"name": "issue_refund", "description": "Requires approval"}])
    trace = parsed(envelope)
    assert trace.messages[0].role == "system" and trace.messages[0].content == "visible policy"
    assert trace.messages[0].source_refs[0].attribute == "gen_ai.system_instructions"
    trace.outcome = "failed"
    unified = to_unified(trace)
    assert "Requires approval" not in unified["metadata"]["task_description"]
    assert "Requires approval" not in unified["messages"][1]["content"]
    assert "Requires approval" in unified["messages"][2]["content"]


def test_same_system_instruction_in_both_fields_is_not_duplicated(envelope):
    call = spans(envelope)[0]
    spans(envelope)[:] = [call]
    set_attribute(call, "gen_ai.system_instructions", [{"type": "text", "content": "policy"}])
    set_attribute(call, "gen_ai.input.messages", [{"role": "system", "parts": [{"type": "text", "content": "policy"}]}, {"role": "user", "content": "task"}])
    trace = parsed(envelope)
    assert len(trace.messages) == 3
    assert len(trace.messages[0].source_refs) == 2


def test_agent_wrapper_is_kept_as_source_without_duplicating_leaf_calls(envelope):
    wrapper = copy.deepcopy(spans(envelope)[0])
    wrapper["spanId"] = "f" * 16
    wrapper["startTimeUnixNano"] = str(int(wrapper["startTimeUnixNano"]) - 100)
    wrapper["endTimeUnixNano"] = str(int(spans(envelope)[-1]["endTimeUnixNano"]) + 100)
    attribute(wrapper, "gen_ai.operation.name")["value"] = {"stringValue": "invoke_agent"}
    for span in spans(envelope):
        span["parentSpanId"] = wrapper["spanId"]
    spans(envelope).append(wrapper)
    trace = parsed(envelope)
    assert len(trace.messages) == 4 and len(trace.otel.spans) == 4
    assert any("外层" in warning for warning in trace.otel.warnings)


def test_missing_output_is_not_fabricated(envelope):
    call = spans(envelope)[0]
    spans(envelope)[:] = [call]
    call["attributes"] = [a for a in call["attributes"] if a["key"] != "gen_ai.output.messages"]
    trace = parsed(envelope)
    assert len(trace.messages) == 1 and trace.messages[0].role == "user"
    assert any("未采集输出" in warning for warning in trace.otel.warnings)


def test_ambiguous_tool_sources_are_not_presented_as_definite_evidence(envelope):
    duplicate = copy.deepcopy(spans(envelope)[1])
    duplicate["spanId"] = "d" * 16
    spans(envelope).append(duplicate)
    trace = parsed(envelope)
    assert len(trace.messages[2].source_refs) == 1
    assert any("匹配多个工具 span" in warning for warning in trace.otel.warnings)


def test_existing_message_format_keeps_priority_over_telemetry_attachment():
    raw = {"messages": [{"role": "user", "content": "existing task"}], "resourceSpans": []}
    trace = parse_upload(json.dumps(raw).encode(), "messages.json")[0]
    assert trace.messages[0].content == "existing task" and trace.otel is None


@pytest.mark.parametrize("problem,match", [
    ("parallel", "并发"), ("history", "历史"), ("choices", "多个输出候选"),
    ("missing", "未采集消息内容"), ("output_only", "缺少输入"),
    ("media", "暂不支持消息部分"), ("id", "traceId"), ("time", "结束时间"),
    ("parent", "parentSpanId"), ("cycle", "循环"), ("conflict", "冲突"),
    ("malformed", "AnyValue"), ("role", "role"), ("bad_json", "有效的 JSON"),
])
def test_ambiguous_or_malformed_telemetry_is_rejected(envelope, problem, match):
    first, _, last = spans(envelope)
    if problem == "parallel":
        last["startTimeUnixNano"] = first["startTimeUnixNano"]
    elif problem == "history":
        set_attribute(last, "gen_ai.input.messages", [{"role": "user", "content": "a different session"}])
    elif problem == "choices":
        set_attribute(first, "gen_ai.output.messages", [{"role": "assistant", "content": "a"}, {"role": "assistant", "content": "b"}])
    elif problem == "missing":
        for span in spans(envelope):
            span["attributes"] = [{"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}}]
    elif problem == "output_only":
        first["attributes"] = [a for a in first["attributes"] if a["key"] != "gen_ai.input.messages"]
    elif problem == "media":
        set_attribute(first, "gen_ai.input.messages", [{"role": "user", "parts": [{"type": "image", "url": "x"}]}])
    elif problem == "id":
        first["traceId"] = "invalid"
    elif problem == "time":
        first["endTimeUnixNano"] = "1"
    elif problem == "parent":
        first["parentSpanId"] = 9
    elif problem == "cycle":
        first["parentSpanId"] = first["spanId"]
    elif problem == "conflict":
        other = copy.deepcopy(first)
        other["name"] = "different"
        spans(envelope).append(other)
    elif problem == "malformed":
        attribute(first, "gen_ai.input.messages")["value"] = {"stringValue": "x", "intValue": "1"}
    elif problem == "role":
        set_attribute(first, "gen_ai.input.messages", [{"role": "invalid", "content": "x"}])
    elif problem == "bad_json":
        attribute(first, "gen_ai.input.messages")["value"] = {"stringValue": "not-json"}
    with pytest.raises(ValueError, match=match):
        parsed(envelope)


def test_mixed_frontend_upload_is_atomic_idempotent_and_persists_sources(tmp_path, envelope):
    app = create_app(Settings(data_dir=tmp_path, base_url="http://127.0.0.1:9/v1", model="fixture"))
    # No lifespan: validate queue submission without making model calls.
    client = TestClient(app)
    native = {"messages": [{"step": 0, "role": "assistant", "content": "native"}], "metadata": {"dataset": "native", "task_id": "native", "reward": 1, "annotation": {}}}
    generic = {"messages": [{"role": "user", "content": "generic"}, {"role": "assistant", "content": "answer"}]}
    files = [("files", ("otel.json", json.dumps(envelope), "application/json")),
             ("files", ("native.json", json.dumps(native), "application/json")),
             ("files", ("messages.jsonl", "\n".join(json.dumps(m) for m in generic["messages"]), "application/x-ndjson"))]
    response = client.post("/api/import", files=files, data={"outcome": "failed"})
    assert response.status_code == 200, response.text
    ids = response.json()["trace_ids"]
    assert len(ids) == 3
    assert client.post("/api/import", files=files, data={"outcome": "failed"}).json()["trace_ids"] == ids
    trace = client.get("/api/traces/" + ids[0]).json()["data"]
    assert trace["outcome"] == "failed" and trace["otel"]["spans"]
    assert client.get("/api/traces/" + ids[1]).json()["data"]["outcome"] == "succeeded"
    assert client.get(f"/api/traces/{ids[0]}/otel").json()["spans"][0]["span"] == spans(envelope)[0]
    assert client.get(f"/api/traces/{ids[1]}/otel").status_code == 404
    queued = client.post("/api/jobs", json={"trace_ids": ids, "mode": "live"})
    assert queued.status_code == 200
    job_id = queued.json()["jobs"][0]["id"]
    app.state.store.update(job_id, status="completed", progress=100, result=demo_report())
    library = client.get('/api/trace-library?input_format=otel_genai&diagnosis=completed').json()
    assert [row['id'] for row in library['items']] == [ids[0]]
    assert library['items'][0]['input_format'] == 'otel_genai'
    history = client.get(f'/api/trace-library/{ids[0]}/history').json()
    assert history['diagnoses'][0]['job_id'] == job_id and history['diagnoses'][0]['run_id'] is None
    exported = client.get(f"/api/jobs/{job_id}/export?format=json").json()
    assert exported["trace"]["data"]["otel"] == trace["otel"]
    markdown = client.get(f"/api/jobs/{job_id}/export?format=md").text
    assert "OTel GenAI 来源" in markdown and trace["otel"]["trace_id"] in markdown
    reloaded = TestClient(create_app(Settings(data_dir=tmp_path)))
    bundle = reloaded.get(f"/api/traces/{ids[0]}/otel").json()
    assert bundle["spans"] == trace["otel"]["spans"]
    assert bundle["message_sources"][0]["source_refs"] == trace["messages"][0]["source_refs"]
    modified = copy.deepcopy(trace)
    modified["otel"]["warnings"].append("display-only change")
    assert fingerprint(Trajectory.model_validate(trace)) == fingerprint(Trajectory.model_validate(modified))
    before = len(client.get("/api/traces").json())
    broken = [("files", ("valid.json", json.dumps({"messages": [{"role": "user", "content": "new"}]}))), ("files", ("bad.json", json.dumps({"resourceSpans": "invalid"})))]
    assert client.post("/api/import", files=broken).status_code == 422
    assert len(client.get("/api/traces").json()) == before
