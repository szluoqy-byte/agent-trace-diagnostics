from copy import deepcopy
import asyncio
import json
from threading import Lock
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from test_trajdebug import pipeline_trace, provider
from test_feedback import provider as feedback_provider, seed_job
from tracelens.config import Settings
from tracelens.diagnosis.agentdebugx import diagnose_agentdebugx, locate_engine_quote, to_agentdebugx
from tracelens.diagnosis.agentdebugx_prompts import ZH_PROFILE, EN_PROFILE
from tracelens.diagnosis.compare import diagnose_dual
from tracelens.diagnosis.synthesis import SynthesisAnswer, ground_answer, parse_synthesis_answer, project_synthesis, synthesize
from tracelens.engine import diagnose
from tracelens.main import create_app
from tracelens.feedback import generate_feedback
from tracelens.model_concurrency import ModelSlots
from tracelens.model_settings_api import test_connection as probe_model
from tracelens.store import Store


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
    if system.startswith(("You synthesize Agent", "你负责对 Agent 轨迹诊断做综合分析")):
        payload = json.loads(user) if user else {}
        engines = payload.get("conclusions", ["trajdebug", "agentdebugx"])
        preferred = "chief" if "chief" in engines else "agentdebugx"
        refs = [key for key, ref in payload.get("evidence", {}).items() if ref["role"] == "candidate"]
        return {"summary": "The diagnosis concerns an unauthorized refund.",
                "evidence_ids": refs or ["E1", "E2"],
                "assessments": {engine: {"support": "high" if engine == preferred else "medium",
                                         "reason": "Direct source excerpt."} for engine in engines}}
    return agentdebugx_answer(system)


def test_synthesis_cannot_select_candidate_with_unverified_evidence():
    answer = SynthesisAnswer.model_validate(dual_answer("You synthesize Agent"))
    candidates = {"trajdebug": {"critical_step": 1}, "agentdebugx": {"critical_step": 3}}
    evidence = {"E1": {"engine": "trajdebug", "step": 1, "quote": "verified"}}
    result = ground_answer(answer, candidates, evidence, {"state":"different", "primary":"trajdebug"})
    assert result["preferred_engine"] == "trajdebug" and result["critical_step"] == 1
    assert "confidence" not in result
    assert result["assessments"]["agentdebugx"]["support"] == "insufficient"
    assert result["evidence"] == [evidence["E1"]]


def test_synthesis_accepts_fenced_json_and_normalizes_supported_labels():
    response = {"summary":"分析结论", "evidence_ids":None,
                "assessments":{"trajdebug":{"support":" 高 ", "reason":"有原文"},
                               "agentdebugx":{"support":"MEDIUM", "reason":"依据较弱"}}}
    engines = ["trajdebug", "agentdebugx"]
    answer = parse_synthesis_answer("评估如下：\n```json\n" + json.dumps(response) + "\n```", aligned=False, engines=engines)
    assert answer.assessments["trajdebug"].support == "high"
    assert answer.assessments["agentdebugx"].support == "medium"
    assert answer.evidence_ids == []
    with pytest.raises(ValueError):
        parse_synthesis_answer('{"summary":"output one"}\n{"summary":"output two"}', aligned=True, engines=engines)
    with pytest.raises(ValueError):
        parse_synthesis_answer('{"summary":"missing assessments","evidence_ids":[]}', aligned=False, engines=engines)
    with pytest.raises(ValueError, match="participating engines"):
        parse_synthesis_answer(json.dumps(response), aligned=False, engines=[*engines, "chief"])


@pytest.mark.parametrize(("failure","recover"), [
    ("format", True), ("length", True), ("empty", True), ("format", False), ("filtered", False),
])
async def test_synthesis_format_retry_is_bounded_and_accounts_for_usage(tmp_path, provider, failure, recover):
    endpoint, requests, state = provider
    outcomes = {"format": ("stop", '{"summary":"Missing assessments"}'),
                "length": ("length", '{"summary":"Truncated'),
                "empty": ("stop", None), "filtered": ("content_filter", None)}

    def override(response):
        if len(requests) == 1 or not recover:
            reason, text = outcomes[failure]
            response["choices"][0]["finish_reason"] = reason
            response["choices"][0]["message"]["content"] = text

    state.update(extra_request_reply=dual_answer, override_response=override)
    settings = Settings(data_dir=tmp_path, base_url=endpoint, model="fixture", max_tokens=6000,
                        prompt_language="en", max_retries=0)
    reports = {"trajdebug": {"attribution":{"critical_step":1}},
               "agentdebugx": {"attribution":{"critical_step":3}}}
    result = await synthesize(pipeline_trace(), reports, dict.fromkeys(reports, []), settings,
                              {"state":"different", "primary":"trajdebug"})
    expected = 1 if failure == "filtered" else 2
    assert result["calls"] == result["requests"] == len(requests) == expected
    assert result["prompt_tokens"] == 10 * expected and result["completion_tokens"] == 5 * expected
    if recover:
        assert result["status"] == "completed" and result["critical_step"] == 3
        latest = requests[-1]["request"]
        assert latest["max_tokens"] == (6000 if failure == "length" else 4096)
        assert "did not pass format validation" in latest["messages"][0]["content"]
    else:
        assert result["status"] == "failed" and result["failure"] == failure


async def test_all_online_model_paths_share_the_workspace_limit(tmp_path, provider, feedback_provider):
    endpoint, calls, state = provider
    feedback_url, feedback_calls, feedback_state = feedback_provider
    lock = Lock()
    active = peak = 0

    def observe():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        sleep(0.08)
        with lock:
            active -= 1

    state.update(critical_step=1, extra_request_reply=dual_answer, observe_request=observe)
    feedback_state["observe_request"] = observe
    settings = Settings(data_dir=tmp_path, base_url=endpoint, model="fixture", llm_concurrency=8,
                        max_retries=0, timeout=5)
    feedback_settings = Settings(data_dir=tmp_path, base_url=feedback_url, model="feedback-fixture",
                                 llm_concurrency=8, max_retries=0, timeout=5)
    store = Store(tmp_path)
    store.save_runtime_settings({"llm_concurrency": 1})
    job, _ = seed_job(store, feedback_settings)

    async def progress(*_):
        pass

    report, feedback, probe = await asyncio.wait_for(asyncio.gather(
        diagnose_dual(pipeline_trace(), settings, progress, diagnose),
        generate_feedback(job, feedback_settings, uuid4().hex),
        probe_model(endpoint, settings.model, settings.api_key, {}, settings),
    ), timeout=20)
    assert {run["status"] for run in report["engine_runs"].values()} == {"completed"}
    assert report["synthesis"]["status"] == "completed" and feedback["usage"]["requests"] == 1 and probe["ok"]
    assert len(feedback_calls) == 1 and len(calls) > 3
    assert peak == 1


def test_workspace_model_limit_can_shrink_below_occupied_slots(tmp_path):
    store = Store(tmp_path)
    slots = ModelSlots(tmp_path, 3)
    leases = [slots.try_acquire() for _ in range(3)]
    assert all(leases) and slots.try_acquire() is None
    store.save_runtime_settings({"llm_concurrency": 1})
    leases[0].release()
    assert slots.try_acquire() is None
    leases[1].release()
    assert slots.try_acquire() is None
    leases[2].release()
    lease = slots.try_acquire()
    assert lease
    lease.release()


def test_aligned_step_is_final_and_disagreement_ties_are_unresolved():
    answer = SynthesisAnswer.model_validate(dual_answer("You synthesize Agent"))
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


@pytest.mark.parametrize("supports, evidence_ids, expected", [
    (("high", "medium", "high"), ["E1", "E2", "E3"], ("trajdebug", 14)),
    (("high", "high", "high"), ["E1", "E2", "E3"], ("trajdebug", 14)),
    (("high", "medium", "high"), ["E1", "E3"], (None, None)),
    (("medium", "medium", "high"), ["E1", "E2", "E3"], ("chief", 12)),
    (("high", "high", "high"), [], (None, None)),
])
def test_synthesis_ranks_verified_support_by_step(supports, evidence_ids, expected):
    steps = {"trajdebug": 14, "agentdebugx": 14, "chief": 12}
    candidates = {engine: {"critical_step": step} for engine, step in steps.items()}
    evidence = {f"E{index}": {"engine": engine, "step": step, "quote": f"原文 #{step}"}
                for index, (engine, step) in enumerate(steps.items(), 1)}
    assessments = {engine: {"support": support, "reason": "依据轨迹原文"}
                   for engine, support in zip(steps, supports, strict=True)}
    answer = SynthesisAnswer.model_validate({"summary": "三路诊断方向一致", "assessments": assessments,
                                            "evidence_ids": evidence_ids})
    result = ground_answer(answer, candidates, evidence, {"state": "different", "primary": "trajdebug"})
    assert (result["preferred_engine"], result["critical_step"]) == expected
    if "E2" not in evidence_ids:
        assert result["assessments"]["agentdebugx"]["support"] == "insufficient"


@pytest.mark.parametrize("corroborated", [False, True])
def test_conflict_uses_ranked_candidate_in_review_task_list_feedback_and_export(tmp_path, corroborated):
    app = create_app(Settings(data_dir=tmp_path, base_url="http://127.0.0.1:9/v1", model="fixture"))
    store = app.state.store
    trace_id = store.add_trace(pipeline_trace())["id"]
    client = TestClient(app)
    task = client.post("/api/tasks", json={"name":"分歧轨迹", "trace_ids":[trace_id], "start":True,
                                          "diagnosis_mode":"deep" if corroborated else "dual"}).json()
    base = f"/api/tasks/{task['task_id']}/runs/{task['run_id']}/traces"
    job_id = store.run_rows(task["run_id"])[0]["job_id"]
    reports = {
        "schema_version":2, "mode":"live", "algorithm":"TrajDebug",
        "attribution":{"critical_step":1, "summary":"标准结论", "category":"type-a", "reasoning":"缺少审批"},
        "comparison":{"state":"different","primary":"trajdebug"},
        "engine_runs":{"trajdebug":{"status":"completed"}, "agentdebugx":{"status":"completed", "report":{
            "algorithm":"AgentDebugX", "attribution":{"critical_step":3, "summary":"候选结论", "category":"type-b"},
        }}},
        "synthesis":{"status":"completed", "critical_step":1, "preferred_engine":"trajdebug", "summary":"综合结论",
                     "assessments":{"trajdebug":{"support":"low"}, "agentdebugx":{"support":"high"}}},
    }
    expected = (3, "type-b")
    if corroborated:
        reports["comparison"]["engines"] = ["trajdebug", "agentdebugx", "chief"]
        reports["engine_runs"]["agentdebugx"]["report"]["attribution"]["critical_step"] = 1
        reports["engine_runs"]["chief"] = {"status":"completed", "report":{
            "algorithm":"CHIEF", "attribution":{"critical_step":3, "summary":"末步仍有异常", "category":""},
        }}
        reports["synthesis"].update(critical_step=None, preferred_engine=None, assessments={
            "trajdebug":{"support":"high"}, "agentdebugx":{"support":"medium"}, "chief":{"support":"high"},
        })
        expected = (1, "type-a")
    store.update(job_id, status="completed", result=reports)
    item = client.get(base, params={"review":"pending"}).json()["items"][0]
    assert (item["critical_step"], item["category"]) == expected
    assert item["feedback_applicable"] is True
    view = client.get(f"{base}/{trace_id}").json()["job"]
    assert view["review_status"] is None and view["result"]["synthesis"]["critical_step"] == expected[0]
    assert view["feedback_eligible"] is True
    exported = client.get(f"/api/jobs/{job_id}/export").json()
    assert exported["job"]["result"]["synthesis"]["critical_step"] == expected[0]
    assert store.job(job_id)["result"] == reports
    if corroborated:
        tied = deepcopy(reports)
        tied["synthesis"]["assessments"]["agentdebugx"]["support"] = "insufficient"
        store.update(job_id, result=tied)
        item = client.get(base).json()["items"][0]
        assert item["critical_step"] is None and item["step_pending_review"] is True
        assert client.post(f"/api/jobs/{job_id}/feedback").status_code == 409
        store.update(job_id, result=reports)
    response = client.post(f"/api/jobs/{job_id}/reviews", json={
        "decision":"modified", "summary":"人工判断", "critical_step":2, "category":"type-review",
    })
    assert response.status_code == 200, response.text
    item = client.get(base, params={"review":"modified"}).json()["items"][0]
    assert item["critical_step"] == 2 and item["summary"] == "人工判断"
    assert client.get(f"/api/jobs/{job_id}").json()["feedback_eligible"] is True
    response = client.post(f"/api/jobs/{job_id}/reviews", json={
        "decision":"rejected", "summary":"人工驳回", "critical_step":None, "category":"",
    })
    assert response.status_code == 200, response.text
    item = client.get(base, params={"review":"rejected"}).json()["items"][0]
    assert item["critical_step"] is None and item["feedback_applicable"] is False
    assert client.post(f"/api/jobs/{job_id}/feedback").status_code == 409


@pytest.mark.parametrize("same_step", [False, True])
async def test_synthesis_calls_model_without_engine_quotes(tmp_path, provider, same_step):
    endpoint, requests, state = provider

    def answer(system, user):
        reply = dual_answer(system, user)
        if same_step:
            reply.pop("assessments")
        return reply

    state["extra_request_reply"] = answer
    settings = Settings(data_dir=tmp_path, base_url=endpoint, model="fixture", prompt_language="zh", max_retries=0)
    reports = {"trajdebug": {"attribution":{"critical_step":1}},
               "agentdebugx": {"attribution":{"critical_step":1 if same_step else 3}}}
    result = await synthesize(pipeline_trace(), reports, dict.fromkeys(reports, []), settings,
                              {"state":"same_step" if same_step else "different", "primary":"trajdebug"})
    assert len(requests) == 1 and result["status"] == "completed" and "confidence" not in result
    assert result["preferred_engine"] == ("trajdebug" if same_step else "agentdebugx")
    assert result["critical_step"] == (1 if same_step else 3)
    assert ("assessments" in result) is not same_step
    messages = requests[0]["request"]["messages"]
    assert "最终分析结论" in messages[0]["content"]
    evidence = json.loads(messages[1]["content"])["evidence"]
    assert any(ref["role"] == "candidate" and ref["quote"] == "issue_refund()" for ref in evidence.values())
    assert any(ref["role"] == "outcome_context" for ref in evidence.values())


@pytest.mark.parametrize("synthesis_status", ["completed", "failed"])
def test_saved_step_agreement_shows_key_step_and_waives_review(tmp_path, synthesis_status):
    app = create_app(Settings(data_dir=tmp_path, base_url="http://127.0.0.1:9/v1", model="fixture"))
    store = app.state.store
    trace_id = store.add_trace(pipeline_trace())["id"]
    client = TestClient(app)
    task = client.post("/api/tasks", json={"name":"一致轨迹", "trace_ids":[trace_id],
                                          "start":True, "diagnosis_mode":"dual_chief"}).json()
    route = f"/api/tasks/{task['task_id']}/runs/{task['run_id']}/traces"
    job_id = store.run_rows(task["run_id"])[0]["job_id"]
    attribution = {"critical_step":1, "summary":"缺少授权", "reasoning":"发起了未授权退款", "category":"type-a"}
    synthesis = {"status": synthesis_status}
    if synthesis_status == "completed":
        synthesis.update(summary="两路均定位到退款调用", critical_step=None,
                         assessments={"trajdebug":{"support":"medium"}, "agentdebugx":{"support":"medium"}})
    report = {
        "schema_version":2, "mode":"live", "algorithm":"TrajDebug", "attribution":attribution,
        "comparison":{"state":"same_step","primary":"trajdebug"},
        "engine_runs":{"trajdebug":{"status":"completed"}, "agentdebugx":{"status":"completed", "report":{
            "algorithm":"AgentDebugX", "attribution":attribution,
        }}, "chief":{"status":"completed", "report":{"algorithm":"CHIEF", "attribution":attribution}}},
        "synthesis":synthesis,
    }
    store.update(job_id, status="completed", result=report)
    detail = client.get(f"{route}/{trace_id}").json()["job"]
    assert detail["review_status"] == "waived" and detail["result"]["attribution"]["critical_step"] == 1
    if synthesis_status == "completed":
        assert detail["result"]["synthesis"]["critical_step"] == 1
        assert "assessments" not in detail["result"]["synthesis"]
    assert client.get(route, params={"review":"waived"}).json()["items"][0]["critical_step"] == 1
    assert client.get(route, params={"review":"pending"}).json()["total"] == 0
    markdown = client.get(f"/api/jobs/{job_id}/export?format=md").text
    assert "人工复核：免复核" in markdown and "关键步骤：#1" in markdown


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
    assert report["duration_seconds"] >= 0
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


@pytest.mark.parametrize("missing_quote", [False, True])
async def test_dual_runs_in_parallel_and_preserves_primary_when_second_engine_fails(monkeypatch, tmp_path, missing_quote):
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
        return {"algorithm": "AgentDebugX", "attribution": {"critical_step": 1}, "evidence": [] if missing_quote else [
            {"step": 1, "original_matched": True},
        ]}

    async def progress(*_):
        pass

    monkeypatch.setattr("tracelens.diagnosis.compare.diagnose_agentdebugx", candidate)
    result = await diagnose_dual(pipeline_trace(), Settings(data_dir=tmp_path), progress, standard)
    assert result["comparison"] == {"state": "same_step" if missing_quote else "aligned", "primary": "trajdebug",
                                    "engines": ["trajdebug", "agentdebugx"]}
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
        assert synthesis["duration_seconds"] >= 0 and synthesis["requests"] == 1
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
