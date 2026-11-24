import json
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from test_trajdebug import provider

from tracelens.config import Settings
from tracelens.deepagent import source_bundle
from tracelens.main import create_app
from tracelens.normalize import fingerprint, parse_upload
from tracelens.trajdebug import to_unified


def span(span_id, kind, start, end, *, parent=None, request=None, response=None):
    return {"trace_id": "trace-1", "span_id": span_id, "parent_id": parent, "start_time": start,
            "end_time": end, "ai_span_kind": kind, "span_input": json.dumps(request), "span_output": json.dumps(response)}


def completion(content, finish, **fields):
    return {"choices": [{"message": {"role": "assistant", "content": content, **fields}, "finish_reason": finish}]}


def export(spans):
    return {"status": "OK", "result": {"totalSpans": len(spans), "spans": spans}}


def test_deepagent_keeps_main_agent_and_marks_terminal_content_filter():
    payload = export([
        span("llm", "LLM", 4, 5, parent="step", request={"messages": [
            {"role": "system", "content": "Policy"}, {"role": "user", "content": "Read status"},
        ]}, response=completion("", "ai_security_guard_input")),
        span("helper", "LLM", 2, 3, parent="agent", response=completion("Generated title", "stop")),
        span("agent", "AGENT", 0, 7, request={"message": "Read status", "permission": "internal metadata"},
             response={"info": json.dumps({"finish": "content-filter"}), "parts": json.dumps([])}),
        span("step", "STEP", 1, 6, parent="agent"),
    ])
    [trace] = parse_upload(json.dumps(payload).encode(), "agent.json")
    assert trace.outcome == "failed"
    assert trace.deepagent.spans == payload["result"]["spans"]
    assert trace.deepagent.export_metadata == {"status": "OK", "result": {"totalSpans": 4}}
    unified = to_unified(trace)
    assert unified["metadata"]["reward"] == 0
    assert [m.source_step for m in trace.messages] == ["helper:span", "llm:span", "step:span", "agent:span"]
    helper, llm, _, agent = trace.messages
    assert "Generated title" in helper.content and not helper.judgable
    assert "[Input]\nsystem: Policy\nuser: Read status" in llm.content
    assert "[Output]" in llm.content
    assert llm.judgable and "[Finish reason]\nai_security_guard_input" in llm.content
    assert agent.judgable and "[Finish reason]\ncontent-filter" in agent.content
    assert [node["span_id"] for node in source_bundle(trace.model_dump())["nodes"]] == ["agent", "step", "helper", "llm"]
    assert "internal metadata" not in json.dumps(unified)
    assert fingerprint(trace) == fingerprint(trace.model_copy(update={"deepagent": None}))


def test_deepagent_reconstructs_model_history_and_keeps_unknown_outcome():
    start = {"role": "user", "content": "Read status"}
    tool_calls = [{"id": "call_1", "function": {"name": "read_status", "arguments": "{}"}}]
    tool_result = {"role": "tool", "tool_call_id": "call_1", "content": "ready"}
    payload = export([
        span("agent", "AGENT", 0, 7, request={"message": "Read status"}, response={
            "info": json.dumps({"finish": "stop"}), "parts": json.dumps([{"type": "text", "text": "Ready"}]),
        }),
        span("model-first", "LLM", 1, 2, parent="agent", request={"messages": [start]},
             response=completion(None, "tool_calls", tool_calls=tool_calls)),
        span("tool-run", "TOOL", 2, 3, parent="agent", response={"status": "ready"}),
        span("model-next", "LLM", 4, 5, parent="agent", request={"messages": [
            start, {"role": "assistant", "tool_calls": tool_calls}, tool_result,
        ]}, response=completion("Ready", "stop")),
    ])
    native = {"messages": [start], "outcome": "failed"}
    trace, other = parse_upload(json.dumps([payload, native]).encode(), "mixed.json")
    assert trace.outcome == "unknown" and other.outcome == "failed"
    first, tool, second, agent = trace.messages
    assert [m.role for m in trace.messages] == ["assistant", "tool", "assistant", "tool"]
    assert "[Input]\nuser: Read status" in first.content and "read_status" in first.content
    assert tool.source_step == "tool-run:span" and tool.judgable
    assert '[Output]\n{"status": "ready"}' in tool.content
    assert "[Input]\ntool: tool_call_id: call_1\nready" in second.content
    assert "[Output]\nReady" in second.content and "[Output]" not in agent.content
    with pytest.raises(ValueError, match="DeepAgent.*result.spans"):
        parse_upload(json.dumps(export([])).encode(), "empty.json")


def test_deepagent_keeps_overlapping_agent_conversations_separate():
    prompt = {"role": "system", "content": "Record status"}
    payload = export([
        span("first", "AGENT", 0, 8), span("second", "AGENT", 1, 7),
        span("first-step", "STEP", 2, 6, parent="first"),
        span("first-model", "LLM", 3, 6, parent="first-step", request={"messages": [prompt]},
             response=completion("First answer", "stop")),
        span("second-model", "LLM", 3, 5, parent="second", request={"messages": [prompt]},
             response=completion("Second answer", "stop")),
    ])
    [trace] = parse_upload(json.dumps(payload).encode(), "agent.json")
    first, second = [m for m in trace.messages if m.role == "assistant"]
    assert [m.source_step for m in (first, second)] == ["second-model:span", "first-model:span"]
    assert "[Input]\nsystem: Record status\n\n[Output]\nSecond answer" in first.content
    assert "[Input]\nsystem: Record status\n\n[Output]\nFirst answer" in second.content


def test_deepagent_orders_parent_starts_before_children_and_preserves_overlaps():
    payload = export([
        span("late", "TOOL", 17, 20, parent="agent", response={"value": "late result"}),
        span("agent", "AGENT", 10, 20),
        {**span("skewed", "RISK_CHECK", 5, 13, parent="agent"), "status_code": "1"},
        span("orphan", "RPC", 8, 14, parent="missing"),
        span("early", "TOOL", 11, 17, parent="agent", request={"query": "read"}, response={"value": "early result"}),
    ])
    [trace] = parse_upload(json.dumps(payload).encode(), "agent.json")
    bundle = source_bundle(trace.model_dump())
    assert [node["span_id"] for node in bundle["nodes"]] == ["orphan", "agent", "skewed", "early", "late"]
    assert [m.source_step for m in trace.messages] == ["skewed:span", "orphan:span", "early:span", "late:span", "agent:span"]
    assert '"parent_span_id": "missing"' in trace.messages[1].content
    assert '[Input]\n{"query": "read"}\n\n[Output]\n{"value": "early result"}' in trace.messages[2].content
    assert all('"start_time"' not in m.content for m in trace.messages)
    assert bundle["nodes"][2]["duration_us"] == 8
    assert bundle["nodes"][2]["status_code"] == "1"
    assert [(node["start_offset_us"], node["end_offset_us"], node["depth"]) for node in bundle["nodes"]] == [
        (3, 9, 0), (5, 15, 0), (0, 8, 1), (6, 12, 1), (12, 15, 1),
    ]


def test_deepagent_rejects_a_cycle_in_the_parent_chain():
    payload = export([span("a", "STEP", 0, 2, parent="b"), span("b", "LLM", 1, 2, parent="a")])
    with pytest.raises(ValueError, match="父子关系包含循环"):
        parse_upload(json.dumps(payload).encode(), "cyclic.json")


def test_deepagent_upload_starts_diagnosis_and_appears_in_library(tmp_path, provider):
    url, calls, state = provider
    payload = export([
        span("agent", "AGENT", 0, 6, request={"message": "Do not refund without approval."},
             response={"info": {"finish": "error"},
                       "parts": [{"type": "text", "text": "A" * 500 + " refund complete"}]}),
        span("llm", "LLM", 2, 4, parent="agent", response=completion("issue_refund()", "stop")),
    ])
    settings = Settings(data_dir=tmp_path, base_url=url, model="fixture", api_key="test", max_retries=0, timeout=5)
    content = json.dumps(payload).encode()
    [projected] = parse_upload(content, "deepagent.json")
    state["trigger_step"] = next(m.step for m in projected.messages if "[Output]\nissue_refund()" in m.content)
    state["critical_step"] = state["trigger_step"]
    files = {"files": ("deepagent.json", content, "application/json")}
    with TestClient(create_app(settings)) as client:
        imported = client.post("/api/import", files=files)
        assert imported.status_code == 200, imported.text
        [trace_id] = imported.json()["trace_ids"]
        preview = client.post("/api/import/preview", files=files).json()
        assert preview["files"][0]["formats"] == ["deepagent"]
        assert preview["rows"][0]["outcome"] == "failed"
        assert preview["rows"][0]["steps"] == 2
        response = client.post("/api/tasks", json={"name": "DeepAgent diagnosis", "preview_id": preview["id"], "start": True})
        assert response.status_code == 200, response.text
        task_id, run_id = (response.json()[key] for key in ("task_id", "run_id"))
        detail_path = f"/api/tasks/{task_id}/runs/{run_id}/traces/{trace_id}"
        deadline = monotonic() + 10
        while True:
            detail = client.get(detail_path).json()
            if detail["job"]["status"] in {"completed", "failed"} or monotonic() >= deadline:
                break
            sleep(0.05)
        assert detail["job"]["status"] == "completed", detail["job"].get("error")
        assert len(detail["job"]["result"]["stage_metrics"]) == 5
        assert detail["trace"]["data"]["input_format"] == "deepagent"
        assert detail["trace"]["data"]["deepagent"] == {"trace_id": "trace-1", "span_count": 2}
        run_id = detail["job"]["result"]["provenance"]["run_id"]
        unified = json.loads((tmp_path / "trajdebug/runs" / run_id / "input.json").read_text(encoding="utf-8"))
        assert len(unified["messages"]) == 2
        assert '"span_id": "llm"' in unified["messages"][0]["content"]
        assert '"span_id": "agent"' in unified["messages"][1]["content"]
        library = client.get("/api/trace-library?input_format=deepagent").json()
        assert [(row["id"], row["input_format"], row["diagnosis"]) for row in library["items"]] == [
            (trace_id, "deepagent", "completed"),
        ]
    assert {call["stage"] for call in calls} == {"compression", "detection", "clustering", "state", "attribution"}


@pytest.mark.parametrize("through_task", [False, True])
def test_reimport_restores_raw_spans_on_matching_trace(tmp_path, through_task):
    agent = span("agent", "AGENT", 0, 6, request={"message": "Read status"}, response={"info": {"finish": "error"}})
    risk = {**span("risk", "RISK_CHECK", 2, 3, parent="agent"), "attributes": '{"details":"原始证据"}'}
    payload = export([risk, agent, risk])
    content = json.dumps(payload).encode()
    [trace] = parse_upload(content, "agent.json")
    app = create_app(Settings(data_dir=tmp_path))
    store = app.state.store
    legacy = store.add_trace(trace.model_copy(update={"deepagent": None}))
    trace_id = legacy["id"]
    existing_job = store.enqueue(trace_id, "live")
    client = TestClient(app)
    path = f"/api/traces/{trace_id}/deepagent"
    assert client.get(path).status_code == 404
    files = {"files": ("agent.json", content, "application/json")}
    if through_task:
        preview = client.post("/api/import/preview", files=files).json()
        task = client.post("/api/tasks", json={"name": "恢复来源", "preview_id": preview["id"]})
        assert task.status_code == 200, task.text
        assert client.get(f"/api/tasks/{task.json()['task_id']}/runs/draft/traces/{trace_id}").status_code == 200
    else:
        assert client.post("/api/import", files=files).json()["trace_ids"] == [trace_id]

    response = client.get(path, params={"download": True})
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.json()["spans"] == payload["result"]["spans"]
    assert response.json()["message_sources"] == [
        {"step": 0, "span_id": "risk", "field": "span"},
        {"step": 1, "span_id": "agent", "field": "span"},
    ]
    assert [node["span_id"] for node in response.json()["nodes"]] == ["agent", "risk"]
    assert store.jobs() == [existing_job]
    assert client.get("/api/trace-library").json()["total"] == 1
