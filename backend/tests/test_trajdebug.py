import asyncio
import copy
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from tracelens.config import Settings
from tracelens.demo import demo_trace, demo_report
from tracelens.engine import diagnose
from tracelens.llm import ModelError
from tracelens.models import Message, Trajectory, RuntimeInput
from tracelens.normalize import normalize, parse_upload
from tracelens.trajdebug import present_report, to_unified, verify_upstream


def test_native_adapter_preserves_routing_and_removes_gold():
    raw = {"messages": [{"step": 0, "role": "user", "content": "visible task", "judgable": False, "compress_role": "preserve"},
                        {"step": 1, "role": "tool", "content": "result", "judgable": False, "compress_role": "compress"}],
           "metadata": {"dataset": "test", "task_id": "routing", "reward": 0, "task_description": "task",
                        "annotation": {"critical_error_step": 1, "human_rationale": "SECRET GOLD"},
                        "extra": {"agent_framework_description": "A tool-using agent", "hidden": "SECRET"}}}
    trace = normalize(raw)
    unified = to_unified(trace)
    assert unified["messages"][1]["judgable"] is False
    assert unified["messages"][1]["compress_role"] == "compress"
    assert unified["metadata"]["extra"] == {"agent_framework_description": "A tool-using agent"}
    assert "SECRET" not in json.dumps(unified)
    assert len(verify_upstream()["files"]) == 20


def test_unknown_outcome_is_not_silently_treated_as_failure():
    trace = demo_trace()
    trace.outcome = "unknown"
    with pytest.raises(ValueError, match="运行结果"):
        to_unified(trace)


def test_delayed_inputs_are_not_promoted_to_global_task():
    trace = demo_trace()
    trace.runtime_inputs = [RuntimeInput(source_id="later", kind="policy", content="LATER POLICY", provenance="fixture", available_from=5, visibility="agent_visible")]
    unified = to_unified(trace)
    assert "LATER POLICY" not in unified["metadata"]["task_description"]
    assert all("LATER POLICY" not in m["content"] for m in unified["messages"][:5])
    assert "LATER POLICY" in unified["messages"][5]["content"]


def test_unmatched_quotes_and_out_of_pool_pick_are_visible_without_rewriting_output():
    trace = demo_trace()
    official = copy.deepcopy(demo_report()["official_output"])
    official["step_triggers"][0]["wrong_content_quote"] = "fabricated approval"
    official["full_instance_states"][1]["fix_evidence_quote"] = "fabricated repair"
    official["critical_error"].update(unified_critical_step=7, llm_pick_supporting_instance_id=None)
    frozen = copy.deepcopy(official)
    report = present_report(trace, {"final": official}, model="test", usage={})
    assert official == frozen
    assert report["official_output"] == frozen
    assert report["attribution"]["critical_step"] == 7
    assert report["attribution"]["critical_instance_id"] is None
    assert report["triggers"][0]["commitment"]["matched"] is False
    assert report["triggers"][0]["commitment"]["step"] is None
    assert report["warnings"][0].startswith("2 处")
    assert "constraints" not in report and "checks" not in report


def test_origin_relocation_and_parse_failure_are_not_hidden():
    official = copy.deepcopy(demo_report()["official_output"])
    official["full_instance_states"][0].update(qualified_origin_step=7, origin_relocated=True, parse_failed=True)
    report = present_report(demo_trace(), {"final": official}, model="test", usage={})
    assert report["instances"][0]["qualified_origin_step"] == 7
    assert report["instances"][0]["origin_step"] == 4
    assert report["instances"][0]["error_state"] == "unknown"
    assert report["warnings"]


def test_report_projects_category_and_instance_from_final_attribution_source():
    official = copy.deepcopy(demo_report()['official_output'])
    first, second = official['full_instance_states']
    first['qualified_origin_taxonomy_tag'] = 'act.UnsafeOrForbiddenAction'
    critical = official['critical_error']
    critical.update(unified_critical_step=5, llm_pick_step=5, llm_pick_supporting_instance_id=second['instance_id'],
                    unified_critical_taxonomy_tag='env.ToolExecutionFailure', llm_pick_taxonomy_tag='env.ToolExecutionFailure')
    frozen = copy.deepcopy(official)
    report = present_report(demo_trace(), {'final': official}, model='test', usage={})
    assert report['official_output'] == frozen
    assert report['attribution']['category'] == 'env.ToolExecutionFailure'
    assert report['attribution']['critical_instance_id'] == 'I2'

    critical.update(unified_critical_step=4, unified_source='chain_min_v2', unified_channel='agent')
    report = present_report(demo_trace(), {'final': official}, model='test', usage={})
    assert report['attribution']['category'] == 'act.UnsafeOrForbiddenAction'
    assert report['attribution']['critical_instance_id'] == 'I1'

    critical.update(unified_critical_step=7, unified_source='llm_pick', llm_pick_step=7,
                    llm_pick_supporting_instance_id=None, unified_critical_taxonomy_tag=None, llm_pick_taxonomy_tag=None)
    report = present_report(demo_trace(), {'final': official}, model='test', usage={})
    assert report['attribution']['critical_step'] == 7
    assert report['attribution']['category'] == '未提供分类'
    assert report['attribution']['critical_instance_id'] is None


@pytest.fixture
def provider():
    calls = []
    state = {"fail": False, "slow": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = request["messages"]
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if state["slow"]:
                import time
                time.sleep(2)
            if state["fail"]:
                self.send_response(401); self.end_headers(); self.wfile.write(b'{}'); return
            if len(messages) == 1:
                stage = "compression"
                value = {tier: {"agent": "refund complete", "env": ""} for tier in ["th1", "th2", "th3"]}
            elif system.startswith("You are an error-trigger"):
                stage = "detection"
                step = int(re.search(r"CURRENT STEP \(msg (\d+)\)", user)[1])
                value = {"triggers": [{"step": 1, "category": "cat-1", "taxonomy_tag": "act.UnsafeOrForbiddenAction", "attribution": "agent",
                                        "wrong_content_quote": "issue_refund()", "reference_quote": "Do not refund without approval.",
                                        "confidence": "high", "confidence_reasoning": "Refund without approval."}] if step == 1 else []}
            elif system.startswith("You are an error-instance clustering"):
                stage = "clustering"
                value = {"instances": [{"instance_id": 1, "trigger_indices": [0], "attribution": "agent", "category": "cat-1",
                                         "error_content": "unapproved refund", "what_is_being_violated": "approval", "merged_reasoning": "same object"}]}
            elif system.startswith("You are an instance-state"):
                stage = "state"
                value = {"instance_id": 1, "origin_commitment_status": "explicit_wrong_commitment", "qualified_origin_step": 1,
                         "qualified_origin_trigger_id": 0, "qualified_origin_taxonomy_tag": "act.UnsafeOrForbiddenAction",
                         "origin_relocated": False, "exploration_suppressed": False, "fix_status": "active", "state": "regular",
                         "terminal_connection": "irreversible", "chain_membership": True, "state_evidence_quote": "refund complete",
                         "semantic_status": "active", "chain_explanation": "Unapproved refund completed."}
            else:
                stage = "attribution"
                value = {"critical_step": 3, "rationale": "Fixture intentionally chooses outside the upstream pool."}
            calls.append({"stage": stage, "request": request})
            response = {"id": "fixture", "object": "chat.completion", "created": 0, "model": "fixture", "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(value)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(json.dumps(response).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:" + str(server.server_port) + "/v1", calls, state
    server.shutdown(); server.server_close(); thread.join()


def pipeline_trace():
    return Trajectory(title="Fixture", task_id="fixture", outcome="failed", annotation={"critical_error_step": 1, "reason": "SECRET_GOLD"},
                      task_description="Do not refund without approval.", messages=[
                          Message(step=0, role="user", content="Do not refund without approval.", judgable=False),
                          Message(step=1, role="assistant", content="issue_refund()"),
                          Message(step=2, role="tool", content="refunded"),
                          Message(step=3, role="assistant", content="A" * 500 + " refund complete")])


async def test_real_official_five_stages_transport_artifacts_and_cache(tmp_path, provider):
    url, calls, _ = provider
    settings = Settings(data_dir=tmp_path, base_url=url, model="fixture", api_key="test", max_retries=0, timeout=5)
    events = []
    async def progress(stage, value, partial):
        events.append((stage, value))
    report = await diagnose(pipeline_trace(), settings, progress)
    assert {c["stage"] for c in calls} == {"compression", "detection", "clustering", "state", "attribution"}
    assert len(calls) == 7
    assert next(c['request'] for c in calls if c['stage'] == 'clustering').get('response_format') is None
    assert report["usage"]["requests"] == 7
    assert report["usage"]["prompt_tokens"] == 70
    assert report["attribution"]["critical_step"] == 3
    assert report["attribution"]["critical_instance_id"] is None
    assert len(report["stage_metrics"]) == 5
    assert "SECRET_GOLD" not in json.dumps(calls)
    directory = tmp_path / "trajdebug/runs" / report["provenance"]["run_id"]
    assert len(list(directory.glob('input*.json'))) == 6
    assert "api_key" not in json.loads((directory / "settings.json").read_text())
    assert events[-1] == ("诊断完成", 100)
    again = await diagnose(pipeline_trace(), settings, progress)
    assert len(calls) == 7
    assert again["usage"]["requests"] == 0
    assert again["usage"]["cache_hits"] == 7
    assert again["official_output"]["critical_error"]["unified_critical_step"] == 3


async def test_genai_import_runs_the_official_five_stages_without_raw_telemetry_in_prompts(tmp_path, provider):
    from pathlib import Path
    envelope = json.loads((Path(__file__).parents[2] / "examples/otel-genai.json").read_text())
    spans = envelope["resourceSpans"][0]["scopeSpans"][0]["spans"]
    spans[0]["status"]["message"] = "RAW_TELEMETRY_SECRET"
    output = next(a for a in spans[-1]["attributes"] if a["key"] == "gen_ai.output.messages")
    output["value"] = {"stringValue": json.dumps([{"role": "assistant", "parts": [{"type": "text", "content": "A" * 500 + " refund complete"}]}])}
    trace = parse_upload(json.dumps(envelope).encode(), "otel.json")[0]
    trace.outcome = "failed"
    url, calls, _ = provider
    async def progress(*_):
        pass
    report = await diagnose(trace, Settings(data_dir=tmp_path, base_url=url, model="fixture", api_key="test"), progress)
    assert {c["stage"] for c in calls} == {"compression", "detection", "clustering", "state", "attribution"}
    assert "RAW_TELEMETRY_SECRET" not in json.dumps(calls)
    assert report["algorithm"] == "TrajDebug"
    assert report["attribution"]["critical_step"] == 3


async def test_provider_failure_does_not_become_a_clean_report(tmp_path, provider):
    url, _, state = provider
    state["fail"] = True
    settings = Settings(data_dir=tmp_path, base_url=url, model="fixture", api_key="test", max_retries=0, timeout=2)
    async def progress(*_):
        pass
    with pytest.raises(ModelError, match="执行失败"):
        await diagnose(pipeline_trace(), settings, progress)
    assert list((tmp_path / "trajdebug/runs").glob("*/failure.json"))


async def test_successful_short_trace_uses_official_success_short_circuit(tmp_path, provider):
    url, calls, _ = provider
    trace = pipeline_trace()
    trace.outcome = "succeeded"
    trace.messages[3].content = "refund complete"
    async def progress(*_):
        pass
    report = await diagnose(trace, Settings(data_dir=tmp_path, base_url=url, model="fixture", api_key="test"), progress)
    assert calls == []
    assert report["attribution"]["critical_step"] is None
    assert report["triggers"] == []
    assert len(report["stage_metrics"]) == 5


async def test_cancellation_terminates_the_worker_process(tmp_path, provider, monkeypatch):
    url, _, state = provider
    state["slow"] = True
    started = asyncio.Event()
    processes = []
    original = asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr('tracelens.engine.asyncio.create_subprocess_exec', spawn)
    async def progress(*_):
        started.set()
    task = asyncio.create_task(diagnose(pipeline_trace(), Settings(data_dir=tmp_path, base_url=url, model="fixture", api_key="test"), progress))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None
