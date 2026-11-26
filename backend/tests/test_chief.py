import asyncio
import json
from pathlib import Path
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from test_diagnosis_engines import dual_answer
from test_trajdebug import pipeline_trace, provider
from tracelens.config import Settings
from tracelens.diagnosis.chief import diagnose_chief, to_chief
from tracelens.llm import ModelError
from tracelens.main import create_app
from tracelens.model_concurrency import ModelSlots
from tracelens.normalize import parse_upload


def chief_answer(system, user, *, step=1, quote="issue_refund()"):
    if not system.startswith(("Analyze the supplied run", "分析提供的执行轨迹")):
        return dual_answer(system, user)
    chinese = system.startswith("分析")
    if "Evidence Quote JSON:" in user:
        reason = "退款缺少授权。" if chinese else "The refund lacked approval."
        return f'Agent Name: agent\nStep Number: {step}\nReason for Mistake: {reason}\nEvidence Quote JSON: {json.dumps(quote)}'
    if "Candidate Error Steps:" in user:
        return (f"Candidate Error Subtasks: [S1]\nCandidate Error Agents: [agent]\n"
                f"Candidate Error Steps:\n- step_id: {step}\n  agent_in_step: [agent]\n  confidence: 0.75")
    name = "处理退款" if chinese else "Handle refund"
    if "Agents_edges:" in user:
        return f"The Subtask Name: {name}\nAgents_edges: []"
    if "-- Observation:" in user:
        return (f"The Subtask Name: {name}\nAgents:\n- Agent: agent\n  -- Action: Request refund\n"
                "  -- Observation: Approval missing\n  -- Thought: Proceed\n  -- Result: Refund\nData_Flow: []")
    if "Data_Transfer:" in user:
        return "[]"
    return f"The Subtask Name: {name}\nStep Range: 0-3\nThe Oracle: Check approval\nEvidence: Request and result"


@pytest.mark.parametrize("filename", ["deepagent-template.json", "otel-genai-template.json"])
def test_chief_adapter_uses_the_canonical_order_for_supported_uploads(filename):
    template = Path(__file__).parents[2] / "frontend" / "public" / "templates" / filename
    trace, *_ = parse_upload(template.read_bytes(), filename, workbench=True)
    if trace.outcome == "unknown":
        trace.outcome = "failed"
    history = to_chief(trace)["history"]
    assert [event["step"] for event in history] == [f"step{message.step}" for message in trace.messages]
    assert [event["content"] for event in history] == [message.content for message in trace.messages]
    if trace.deepagent:
        by_span = {message.source_step.rpartition(":")[0]: message.step for message in trace.messages}
        spans = {span["span_id"]: span for span in trace.deepagent.spans}
        for message, event in zip(trace.messages, history, strict=True):
            span_id = message.source_step.rpartition(":")[0]
            if span_id in spans:
                assert event.get("parent_step") == by_span.get(spans[span_id].get("parent_id"))


async def test_chief_waits_for_the_shared_model_slot(tmp_path, provider):
    endpoint, requests, state = provider
    state["extra_request_reply"] = chief_answer
    settings = Settings(data_dir=tmp_path, base_url=endpoint, model="fixture", llm_concurrency=1, max_retries=0)
    ready = asyncio.Event()

    async def progress(*_):
        ready.set()

    with ModelSlots.for_settings(settings).request():
        run = asyncio.create_task(diagnose_chief(pipeline_trace(), settings, progress))
        await asyncio.wait_for(ready.wait(), 2)
        await asyncio.sleep(0.1)
        assert not requests
    assert (await asyncio.wait_for(run, 5))["usage"]["requests"] == 6


@pytest.mark.parametrize("language", ["zh", "en"])
async def test_chief_uses_six_localized_stages_and_verified_quotes(tmp_path, provider, language):
    endpoint, requests, state = provider
    state["extra_request_reply"] = chief_answer
    settings = Settings(data_dir=tmp_path, base_url=endpoint, model="fixture", prompt_language=language,
                        api_key="default-key", headers={"authorization": "Bearer custom-key", "X-Tenant": "chief"},
                        max_tokens=2048, max_retries=0)
    events = []

    async def progress(*args):
        events.append(args)

    report = await diagnose_chief(pipeline_trace(), settings, progress)
    assert len(requests) == 6 and report["usage"] == {"requests": 6, "prompt_tokens": 60, "completion_tokens": 30}
    assert all(call["authorization"] == ["Bearer custom-key"] for call in requests)
    assert all(call["headers"]["x-tenant"] == "chief" and call["request"]["max_tokens"] == 2048 for call in requests)
    assert "SECRET_GOLD" not in str(requests) and report["provenance"]["rag"] is False
    assert all(("任务目标：" in call["request"]["messages"][-1]["content"]) == (language == "zh") for call in requests)
    assert report["attribution"]["critical_step"] == 1 and report["root_cause_agent"] == "agent"
    assert report["evidence"][0]["original_matched"]
    assert report["official_output"]["dag_graph"]["subtasks"][0]["agents"][0]["agent"] == "agent"
    assert report["official_output"]["candidates"]["candidate_error_steps"][0]["step_id"] == 1
    assert events[-1][1] == 100


async def test_chief_reports_empty_or_out_of_range_evidence_and_skips_success(tmp_path, provider):
    endpoint, requests, state = provider
    state["extra_request_reply"] = lambda system, user: chief_answer(system, user, quote="refund complete")
    settings = Settings(data_dir=tmp_path, base_url=endpoint, model="fixture", max_retries=0)

    async def progress(*_):
        pass

    trace = pipeline_trace()
    report = await diagnose_chief(trace, settings, progress)
    assert report["attribution"]["critical_step"] == 1
    assert report["evidence"][0]["step"] is None and not report["evidence"][0]["original_matched"]
    state["extra_request_reply"] = lambda system, user: chief_answer(system, user, step=40)
    report = await diagnose_chief(trace, settings, progress)
    assert report["attribution"]["critical_step"] is None and not report["evidence"]
    assert report["official_output"]["candidates"]["candidate_error_steps"] == []
    trace.outcome = "succeeded"
    report = await diagnose_chief(trace, settings, progress)
    assert report["usage"]["requests"] == 0 and len(requests) == 12
    assert report["task_outcome"] == "succeeded"
    trace.outcome = "failed"
    state["agentdebugx_error_status"] = 403
    with pytest.raises(ModelError, match="HTTP 403") as error:
        await diagnose_chief(trace, settings, progress)
    assert error.value.usage["requests"] == 1
    assert "private gateway detail" not in str(error.value)


@pytest.mark.parametrize(("mode", "bad_chief"), [("chief", False), ("dual_chief", False), ("dual_chief", True)])
def test_chief_task_uses_saved_connection_and_keeps_dual_synthesis(tmp_path, provider, mode, bad_chief):
    endpoint, requests, state = provider

    def answer(system, user):
        if bad_chief and "Evidence Quote JSON:" in user:
            return "Unparseable evaluation result."
        return chief_answer(system, user, step=3, quote="refund complete")

    state.update(critical_step=1, extra_request_reply=answer)
    app = create_app(Settings(data_dir=tmp_path))
    trace_id = app.state.store.add_trace(pipeline_trace())["id"]
    with TestClient(app) as client:
        assert client.post("/api/model-configurations", json={
            "name": "Fixture", "base_url": endpoint, "model": "saved-model", "api_key": "fixture-key",
            "headers": {"X-Tenant": "fixture-tenant"}, "make_default": True,
        }).status_code == 201
        assert client.put("/api/runtime-settings", json={"llm_concurrency": 1, "max_retries": 0}).status_code == 200
        created = client.post("/api/tasks", json={
            "name": "CHIEF 根因定位", "trace_ids": [trace_id], "diagnosis_mode": mode,
            "start": True, "prompt_language": "en", "auto_feedback": mode == "chief",
        })
        assert created.status_code == 200, created.text
        task_id, run_id = (created.json()[key] for key in ("task_id", "run_id"))
        route = f"/api/tasks/{task_id}/runs/{run_id}/traces/{trace_id}"
        deadline = monotonic() + 15
        while True:
            job = client.get(route).json()["job"]
            if job["status"] in {"completed", "failed"} or monotonic() >= deadline:
                break
            sleep(0.05)
        assert job["status"] == "completed", job
        native = client.get(f"/api/jobs/{job['id']}/artifacts/chief")
        if bad_chief:
            assert native.status_code == 404
        else:
            assert native.status_code == 200 and native.json()["final"]["mistake_step"] == 3
        report = job["result"]
        task = client.get(f"/api/tasks/{task_id}").json()
        config = next(run["config"] for run in task["runs"] if run["id"] == run_id)
        assert config["diagnosis_mode"] == mode
        assert config["chief_prompt_profile"] == "chief-en-v1"
        assert all(call["headers"]["x-tenant"] == "fixture-tenant" and call["request"]["model"] == "saved-model"
                   for call in requests)
        if mode == "chief":
            assert report["algorithm"] == "CHIEF" and not job["feedback_eligible"]
            assert job["review_status"] is None and report["evidence"][0]["step"] == 3
        else:
            chief = report["engine_runs"]["chief"]
            if bad_chief:
                assert chief["status"] == "failed" and chief["usage"]["requests"] == 6
            else:
                assert chief["report"]["attribution"]["critical_step"] == 3
            assert report["synthesis"]["critical_step"] == 1 and job["review_status"] == "waived"
            assert {item["engine"] for item in report["synthesis"]["evidence"]} == {"trajdebug", "agentdebugx"}
        assert "CHIEF" in client.get(f"/api/jobs/{job['id']}/export?format=md").text
