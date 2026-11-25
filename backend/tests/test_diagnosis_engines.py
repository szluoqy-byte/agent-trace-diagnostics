from copy import deepcopy
import asyncio
import json
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from test_trajdebug import pipeline_trace, provider
from tracelens.config import Settings
from tracelens.diagnosis.agentdebugx import diagnose_agentdebugx, locate_engine_quote, to_agentdebugx
from tracelens.diagnosis.agentdebugx_prompts import ZH_PROFILE, EN_PROFILE
from tracelens.diagnosis.compare import diagnose_dual
from tracelens.diagnosis.synthesis import SynthesisAnswer, ground_answer, project_synthesis, synthesize
from tracelens.engine import diagnose
from tracelens.main import create_app


def agentdebugx_answer(system):
    if system.startswith(("You are an AI assistant tasked with analyzing agent conversation history",
                          "你负责分析 Agent 为解决实际问题执行的轨迹")):
        return {"span_id": "step-1", "step_index": 1, "agent_name": "agent", "confidence": 0.9,
                "rationale": "The refund was requested.", "evidence": ["issue_refund()"]}
    if system.startswith(("You are AgentDebugX-DeepDebug", "你负责撰写 AgentDebugX-DeepDebug")):
        chinese = system.startswith("你负责")
        summary = "退款前应先确认权限" if chinese else "The refund requires approval."
        suggestion = "调用退款 API 前校验授权。" if chinese else "Check approval before calling the refund API."
        return {"summary": summary, "evidence": [{"event_id": "step-1", "quote": "issue_refund()"}],
                "suggestion": suggestion}
    if '"half"' in system and system.startswith(("A run FAILED.", "一次 Agent 运行失败")):
        return {"half": "upper", "confidence": 0.9}
    if system.startswith(("A run FAILED.", "A multi-step agent run FAILED.", "一次 Agent 运行失败", "Agent 运行失败")):
        return {"step": 1, "agent": "agent", "event_id": "step-1", "confidence": 0.9, "candidate": "A"}
    return None


def dual_answer(system, user=None):
    if system.startswith(("You synthesize two Agent", "你负责对两路 Agent 轨迹诊断做综合分析")):
        evidence = json.loads(user)["evidence"] if user else {}
        refs = [key for key, ref in evidence.items() if ref["role"] == "candidate"]
        return {"summary": "Both sources show an unauthorized refund.",
                "evidence_ids": refs or ["E1", "E2"],
                "assessments": {"trajdebug": {"support": "medium", "reason": "Shows the unauthorized action."},
                                "agentdebugx": {"support": "high", "reason": "Direct source excerpt."}}}
    return agentdebugx_answer(system)


def test_synthesis_cannot_select_candidate_with_unverified_evidence():
    answer = SynthesisAnswer.model_validate(dual_answer("You synthesize two Agent"))
    candidates = {"trajdebug": {"critical_step": 1}, "agentdebugx": {"critical_step": 3}}
    evidence = {"E1": {"engine": "trajdebug", "step": 1, "quote": "verified"}}
    result = ground_answer(answer, candidates, evidence, {"state":"different", "primary":"trajdebug"})
    assert result["preferred_engine"] == "trajdebug" and result["critical_step"] == 1
    assert "confidence" not in result
    assert result["assessments"]["agentdebugx"]["support"] == "insufficient"
    assert result["evidence"] == [evidence["E1"]]


def test_aligned_step_is_final_and_disagreement_ties_are_unresolved():
    answer = SynthesisAnswer.model_validate(dual_answer("You synthesize two Agent"))
    candidates = {"trajdebug": {"critical_step": 1}, "agentdebugx": {"critical_step": 1}}
    aligned = ground_answer(answer, candidates, {}, {"state":"aligned", "primary":"trajdebug"})
    assert aligned["critical_step"] == 1
    legacy = {
        "attribution": {"critical_step": 1},
        "comparison": {"state":"different", "primary":"trajdebug"},
        "engine_runs": {"trajdebug":{"status":"completed"}, "agentdebugx":{
            "status":"completed", "report":{"attribution":{"critical_step":3}},
        }},
        "synthesis": {"status":"completed", "critical_step":3, "preferred_engine":"agentdebugx", "confidence":"high",
                      "assessments": {"trajdebug":{"support":"high"}, "agentdebugx":{"support":"high"}}},
    }
    projected = project_synthesis(legacy)["synthesis"]
    assert projected["critical_step"] is None and projected["preferred_engine"] is None
    assert "confidence" not in projected and legacy["synthesis"]["confidence"] == "high"


def test_conflict_uses_ranked_candidate_in_review_and_task_list(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, base_url="http://127.0.0.1:9/v1", model="fixture"))
    store = app.state.store
    trace_id = store.add_trace(pipeline_trace())["id"]
    client = TestClient(app)
    task = client.post("/api/tasks", json={"name":"分歧轨迹", "trace_ids":[trace_id], "start":True,
                                          "diagnosis_mode":"dual"}).json()
    base = f"/api/tasks/{task['task_id']}/runs/{task['run_id']}/traces"
    job_id = store.run_rows(task["run_id"])[0]["job_id"]
    reports = {
        "schema_version":2, "mode":"live", "algorithm":"TrajDebug",
        "attribution":{"critical_step":1, "summary":"标准结论", "category":"type-a"},
        "comparison":{"state":"different","primary":"trajdebug"},
        "engine_runs":{"trajdebug":{"status":"completed"}, "agentdebugx":{"status":"completed", "report":{
            "algorithm":"AgentDebugX", "attribution":{"critical_step":3, "summary":"候选结论", "category":"type-b"},
        }}},
        "synthesis":{"status":"completed", "critical_step":1, "preferred_engine":"trajdebug", "summary":"综合结论",
                     "assessments":{"trajdebug":{"support":"low"}, "agentdebugx":{"support":"high"}}},
    }
    store.update(job_id, status="completed", result=reports)
    item = client.get(base, params={"review":"pending"}).json()["items"][0]
    assert item["critical_step"] == 3 and item["category"] == "type-b"
    view = client.get(f"{base}/{trace_id}").json()["job"]
    assert view["review_status"] is None and view["result"]["synthesis"]["critical_step"] == 3
    response = client.post(f"/api/jobs/{job_id}/reviews", json={
        "decision":"modified", "summary":"人工判断", "critical_step":1, "category":"type-a",
    })
    assert response.status_code == 200, response.text
    item = client.get(base, params={"review":"modified"}).json()["items"][0]
    assert item["critical_step"] == 1 and item["summary"] == "人工判断"


async def test_synthesis_calls_model_without_engine_quotes(tmp_path, provider):
    endpoint, requests, state = provider
    state["extra_request_reply"] = dual_answer
    settings = Settings(data_dir=tmp_path, base_url=endpoint, model="fixture", prompt_language="zh", max_retries=0)
    reports = {"trajdebug": {"attribution":{"critical_step":1}},
               "agentdebugx": {"attribution":{"critical_step":3}}}
    result = await synthesize(pipeline_trace(), reports, dict.fromkeys(reports, []), settings,
                              {"state":"different", "primary":"trajdebug"})
    assert len(requests) == 1 and result["status"] == "completed" and "confidence" not in result
    assert result["preferred_engine"] == "agentdebugx" and result["critical_step"] == 3
    messages = requests[0]["request"]["messages"]
    assert "最终分析结论" in messages[0]["content"]
    evidence = json.loads(messages[1]["content"])["evidence"]
    assert any(ref["role"] == "candidate" and ref["quote"] == "issue_refund()" for ref in evidence.values())
    assert any(ref["role"] == "outcome_context" for ref in evidence.values())


@pytest.mark.parametrize("language", ["en", "zh"])
async def test_upstream_agentdebugx_preserves_grounding_and_language(tmp_path, language, provider, monkeypatch):
    trace = pipeline_trace()
    endpoint, requests, state = provider
    state["extra_reply"] = agentdebugx_answer
    if language == "en":
        endpoint += "/chat/completions/"
    else:
        monkeypatch.delenv("DETECTOR_HTTP_TRUST_ENV", raising=False)
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    settings = Settings(data_dir=tmp_path, base_url=endpoint, model="fixture", api_key="test",
                        prompt_language=language, headers={"X-Test-Tenant": "isolated"}, max_retries=0, timeout=5)

    async def progress(*_):
        pass

    report = await diagnose_agentdebugx(trace, settings, progress)
    assert len(requests) >= 2 and len(requests) == report["usage"]["requests"]
    assert all(("仅返回" in call["request"]["messages"][0]["content"]) == (language == "zh") for call in requests)
    assert all((("任务目标：" in call["request"]["messages"][-1]["content"]) == (language == "zh")) for call in requests)
    assert report["provenance"]["implementation"] == "vendored_source"
    assert report["provenance"]["prompt_profile"] == (ZH_PROFILE if language == "zh" else EN_PROFILE)
    assert all(call["headers"].get("x-test-tenant") == "isolated" for call in requests)
    assert all("SECRET_GOLD" not in str(call["request"]) for call in requests)
    assert report["attribution"]["critical_step"] == 1
    assert report["evidence"][0]["quote"] == "issue_refund()"
    assert report["evidence"][0]["original_matched"]
    assert report["official_output"]["recovery"]["proposal_count"] == 1
    assert report["engine_detection"]["metadata"]["analyzer"] == "HeuristicAnalyzer"


async def test_dual_reports_agentdebugx_http_rejection_without_provider_details(tmp_path, provider):
    endpoint, _, state = provider
    state.update(critical_step=1, extra_reply=agentdebugx_answer, agentdebugx_error_status=403)
    settings = Settings(data_dir=tmp_path, base_url=endpoint, model="fixture", api_key="test", max_retries=0)

    async def progress(*_):
        pass

    result = await diagnose_dual(pipeline_trace(), settings, progress, diagnose)
    assert result["engine_runs"]["trajdebug"]["status"] == "completed"
    bad = result["engine_runs"]["agentdebugx"]
    assert bad["status"] == "failed" and "HTTP 403" in bad["error"]
    assert result["synthesis"] == {"status": "incomplete"}
    assert "private gateway detail" not in json.dumps(result)


def test_engine_input_uses_message_order_and_canonical_visibility():
    trace = pipeline_trace()
    request = to_agentdebugx(trace)
    assert [event["event_id"] for event in request["events"]] == [f"step-{index}" for index in range(len(trace.messages))]
    assert "SECRET_GOLD" not in json.dumps(request)
    assert request["events"][1]["output"] == trace.messages[1].content
    assert request["events"][2]["event_type"] == "tool.result"


def test_quote_normalization_restores_original_text_in_the_reported_event():
    trace = pipeline_trace()
    trace.messages[1].content = "request:\n\nissue_refund()"
    quote = locate_engine_quote(trace, 1, "request: issue_refund()")
    assert quote["original_matched"] and quote["quote"] == trace.messages[1].content
    assert not locate_engine_quote(trace, 2, "request: issue_refund()")["matched"]
    trace.messages[1].content *= 2
    assert not locate_engine_quote(trace, 1, "request: issue_refund()")["matched"]


async def test_dual_runs_in_parallel_and_preserves_primary_when_second_engine_fails(monkeypatch, tmp_path):
    standard_started = asyncio.Event()
    candidate_started = asyncio.Event()
    base = {"algorithm": "TrajDebug", "attribution": {"critical_step": 1}, "triggers": [
        {"commitment": {"step": 1, "original_matched": True}},
    ]}

    async def standard(*_):
        standard_started.set()
        await asyncio.wait_for(candidate_started.wait(), 1)
        return base

    async def candidate(*_):
        candidate_started.set()
        await asyncio.wait_for(standard_started.wait(), 1)
        return {"algorithm": "AgentDebugX", "attribution": {"critical_step": 1}, "evidence": [
            {"step": 1, "original_matched": True},
        ]}

    async def progress(*_):
        pass

    monkeypatch.setattr("tracelens.diagnosis.compare.diagnose_agentdebugx", candidate)
    result = await diagnose_dual(pipeline_trace(), Settings(data_dir=tmp_path), progress, standard)
    assert result["comparison"] == {"state": "aligned", "primary": "trajdebug"}
    assert result["engine_runs"]["agentdebugx"]["report"]["attribution"]["critical_step"] == 1
    assert "comparison" not in base

    async def failing_candidate(*_):
        await candidate()
        raise ValueError("secret provider error")

    monkeypatch.setattr("tracelens.diagnosis.compare.diagnose_agentdebugx", failing_candidate)
    result = await diagnose_dual(pipeline_trace(), Settings(data_dir=tmp_path), progress, standard)
    assert result["comparison"]["state"] == "incomplete"
    assert result["algorithm"] == "TrajDebug"
    assert result["engine_runs"]["agentdebugx"]["status"] == "failed"
    assert "secret" not in str(result)


@pytest.mark.parametrize("override_auth", [False, True])
def test_dual_task_reuses_saved_model_and_keeps_native_export(tmp_path, provider, override_auth):
    endpoint, requests, state = provider
    state.update(critical_step=1, extra_request_reply=dual_answer)
    app = create_app(Settings(data_dir=tmp_path, model="environment-model", base_url="", api_key="environment-key"))
    trace = deepcopy(pipeline_trace())
    trace_id = app.state.store.add_trace(trace)["id"]
    headers = {"X-Test-Tenant": "saved-tenant"}
    if override_auth:
        headers["authorization"] = "Bearer gateway-key"
    model = {"name": "Saved connection", "base_url": endpoint, "model": "saved-model", "api_key": "saved-key",
             "headers": headers, "make_default": True}
    runtime = {"max_retries": 0, "timeout": 5, "max_tokens": 2048, "llm_concurrency": 2}
    with TestClient(app) as client:
        saved = client.post("/api/model-configurations", json=model)
        assert saved.status_code == 201
        assert client.get("/api/config").json()["model"] == model["model"]
        assert client.put("/api/runtime-settings", json=runtime).status_code == 200
        result = client.post("/api/tasks", json={"name": "对照诊断", "trace_ids": [trace_id], "start": True,
                                                "prompt_language": "en", "diagnosis_mode": "dual"})
        assert result.status_code == 200, result.text
        task_id, run_id = (result.json()[key] for key in ("task_id", "run_id"))
        path = f"/api/tasks/{task_id}/runs/{run_id}/traces/{trace_id}"
        deadline = monotonic() + 15
        while True:
            job = client.get(path).json()["job"]
            if job["status"] in ("completed", "failed") or monotonic() > deadline:
                break
            sleep(0.05)
        assert job["status"] == "completed", job
        assert job["review_status"] == "waived" and job["reviews"] == []
        traces = f"/api/tasks/{task_id}/runs/{run_id}/traces"
        waived = client.get(traces, params={"review": "waived"}).json()
        assert waived["total"] == waived["counts"]["review_waived"] == 1
        assert waived["counts"]["reviewed"] == waived["counts"]["review_pending"] == 0
        assert client.get(traces, params={"review": "pending"}).json()["total"] == 0
        assert {r["status"] for r in job["result"]["engine_runs"].values()} == {"completed"}, job["result"]
        assert {call["stage"] for call in requests} >= {"agentdebugx", "detection"}
        expected_auth = headers.get("authorization", "Bearer saved-key")
        assert all(call["authorization"] == [expected_auth] for call in requests)
        assert all(call["headers"].get("x-test-tenant") == "saved-tenant" for call in requests)
        assert all(call["request"]["model"] == model["model"] for call in requests)
        agentdebugx_requests = [call["request"] for call in requests if call["stage"] == "agentdebugx"]
        assert all(call["max_tokens"] <= runtime["max_tokens"] for call in agentdebugx_requests)
        assert {key: job["result"]["execution"][key] for key in runtime} == runtime
        assert job["result"]["comparison"]["primary"] == "trajdebug"
        assert job["result"]["engine_runs"]["agentdebugx"]["report"]["attribution"]["critical_step"] == 1
        synthesis = job["result"]["synthesis"]
        assert synthesis["status"] == "completed" and synthesis["critical_step"] == 1
        assert synthesis["preferred_engine"] == "trajdebug"
        assert "confidence" not in synthesis
        assert {item["engine"] for item in synthesis["evidence"]} == {"trajdebug", "agentdebugx"}
        reviews = [call for call in requests if call["request"]["messages"][0]["content"].startswith("You synthesize")]
        assert len(reviews) == 1 and "SECRET_GOLD" not in str(reviews)
        inputs = json.loads(reviews[0]["request"]["messages"][1]["content"])
        assert inputs["conclusions"]["trajdebug"]["diagnostic_process"]["triggers"]
        assert inputs["conclusions"]["trajdebug"]["diagnostic_process"]["instances"]
        assert inputs["conclusions"]["agentdebugx"]["diagnostic_process"]["audit"]
        assert any(ref["role"] == "process" for ref in inputs["evidence"].values())
        native = client.get(f"/api/jobs/{job['id']}/artifacts/agentdebugx")
        assert native.status_code == 200 and native.json()["diagnosis"]["root_cause_event_id"] == "step-1"
        markdown = client.get(f"/api/jobs/{job['id']}/export?format=md").text
        assert "综合分析" in markdown and "双路诊断对照" in markdown
        assert "人工复核：免复核" in markdown
        selected = job["result"]["attribution"]
        review = client.post(f"/api/jobs/{job['id']}/reviews", json={
            "decision": "confirmed", "summary": synthesis["summary"], "critical_step": synthesis["critical_step"],
            "category": selected["category"], "notes": "reviewed source evidence",
        })
        assert review.status_code == 200, review.text
        assert client.get(path).json()["job"]["review_status"] == "confirmed"
        assert client.get(traces, params={"review": "waived"}).json()["total"] == 0
        task = client.get(f"/api/tasks/{task_id}")
        config = task.json()["runs"][0]["config"]
        assert config["diagnosis_mode"] == "dual"
        assert config["model_connection_id"] == saved.json()["id"]
        assert all(secret not in task.text for secret in ("saved-key", "gateway-key", "saved-tenant"))
