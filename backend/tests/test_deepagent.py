import json
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from test_trajdebug import provider

from tracelens.config import Settings
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
    assert [(m.role, m.content, m.source_step) for m in trace.messages] == [
        ("system", "Policy", "llm:span_input"), ("user", "Read status", "llm:span_input"),
        ("tool", "finish_reason: ai_security_guard_input", "llm:span_output"),
        ("tool", "finish: content-filter", "agent:span_output"),
    ]
    assert to_unified(trace)["metadata"]["reward"] == 0
    assert "Generated title" not in json.dumps(to_unified(trace))
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
        span("model-next", "LLM", 4, 5, parent="agent", request={"messages": [
            start, {"role": "assistant", "tool_calls": tool_calls}, tool_result,
        ]}, response=completion("Ready", "stop")),
    ])
    native = {"messages": [start], "outcome": "failed"}
    trace, other = parse_upload(json.dumps([payload, native]).encode(), "mixed.json")
    assert trace.outcome == "unknown" and other.outcome == "failed"
    assert [m.role for m in trace.messages] == ["user", "assistant", "tool", "assistant"]
    assert "read_status" in trace.messages[1].content
    assert trace.messages[2].content == "tool_call_id: call_1\nready"
    assert trace.messages[3].content == "Ready"
    with pytest.raises(ValueError, match="DeepAgent.*result.spans"):
        parse_upload(json.dumps(export([])).encode(), "empty.json")


def test_deepagent_upload_starts_diagnosis_and_appears_in_library(tmp_path, provider):
    url, calls, _ = provider
    payload = export([
        span("agent", "AGENT", 0, 6, request={"message": "Do not refund without approval."},
             response={"info": {"finish": "error"},
                       "parts": [{"type": "text", "text": "A" * 500 + " refund complete"}]}),
        span("llm", "LLM", 2, 4, parent="agent", response=completion("issue_refund()", "stop")),
    ])
    settings = Settings(data_dir=tmp_path, base_url=url, model="fixture", api_key="test", max_retries=0, timeout=5)
    files = {"files": ("deepagent.json", json.dumps(payload), "application/json")}
    with TestClient(create_app(settings)) as client:
        imported = client.post("/api/import", files=files)
        assert imported.status_code == 200, imported.text
        [trace_id] = imported.json()["trace_ids"]
        preview = client.post("/api/import/preview", files=files).json()
        assert preview["files"][0]["formats"] == ["deepagent"]
        assert preview["rows"][0]["outcome"] == "failed"
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
        library = client.get("/api/trace-library?input_format=deepagent").json()
        assert [(row["id"], row["input_format"], row["diagnosis"]) for row in library["items"]] == [
            (trace_id, "deepagent", "completed"),
        ]
    assert {call["stage"] for call in calls} == {"compression", "detection", "clustering", "state", "attribution"}


@pytest.mark.parametrize("through_task", [False, True])
def test_reimport_restores_raw_spans_on_legacy_trace(tmp_path, through_task):
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
        {"step": 0, "span_id": "agent", "field": "span_input"},
        {"step": 1, "span_id": "agent", "field": "span_output"},
    ]
    assert store.jobs() == [existing_job]
    assert client.get("/api/trace-library").json()["total"] == 1
