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
    state.update(critical_step=1, extra_reply=agentdebugx_answer)
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
        native = client.get(f"/api/jobs/{job['id']}/artifacts/agentdebugx")
        assert native.status_code == 200 and native.json()["diagnosis"]["root_cause_event_id"] == "step-1"
        assert "双路诊断对照" in client.get(f"/api/jobs/{job['id']}/export?format=md").text
        task = client.get(f"/api/tasks/{task_id}")
        config = task.json()["runs"][0]["config"]
        assert config["diagnosis_mode"] == "dual"
        assert config["model_connection_id"] == saved.json()["id"]
        assert all(secret not in task.text for secret in ("saved-key", "gateway-key", "saved-tenant"))
