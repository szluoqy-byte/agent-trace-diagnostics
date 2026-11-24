import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import uuid

from fastapi.testclient import TestClient
import pytest

from tracelens.config import Settings
from tracelens.feedback import generate_feedback
from tracelens.main import create_app
from tracelens.normalize import normalize, parse_upload
from tracelens.store import Store
from tracelens.trajdebug import present_report, to_unified


HINT = 'Before issuing a refund, verify the booking timestamp and obtain explicit approval.'


@pytest.fixture
def provider():
    calls, state = [], {'invalid': False, 'slow': False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            if state['slow']:
                time.sleep(2)
            report = {'conflict_type': 'cat-1', 'taxonomy_tag': 'act.UnsafeOrForbiddenAction',
                      'wrong_content_quote': 'issue_refund', 'reference_quote': 'Do not refund without approval.',
                      'error_explanation': 'Refund submitted without approval.', 'hint_sentence': state.get('hint', HINT),
                      'alternative_critical_steps': [{'step': 3, 'taxonomy_tag': 'reason.InvalidInference',
                                                      'reason': 'The final answer repeats the unsupported claim.',
                                                      'hint_sentence': 'Check the tool response before confirming completion.'}],
                      'cascade_analysis': [], 'narrative_summary': 'An unapproved refund caused the failure.'}
            text = 'not-json' if state['invalid'] else json.dumps(report)
            response = {'id': 'fixture', 'object': 'chat.completion', 'created': 0, 'model': 'fixture',
                        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            try:
                self.wfile.write(json.dumps(response).encode())
            except BrokenPipeError:
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/v1', calls, state
    server.shutdown()
    server.server_close()
    thread.join()


def seed_job(store, settings, *, otel=False):
    if otel:
        envelope = json.loads((Path(__file__).parents[2] / 'examples/otel-genai.json').read_text())
        envelope['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['status']['message'] = 'SECRET_TELEMETRY'
        trace = parse_upload(json.dumps(envelope).encode(), 'otel.json')[0]
        trace.outcome = 'failed'
    else:
        trace = normalize({'task_id': 'test', 'outcome': 'failed', 'messages': [
            {'role': 'user', 'content': 'Do not refund without approval.'},
            {'role': 'assistant', 'content': 'issue_refund()'},
            {'role': 'tool', 'content': 'refunded'},
            {'role': 'assistant', 'content': 'refund complete'}]})
    trace.annotation = {'human_rationale': 'SECRET_GOLD'}
    saved = store.add_trace(trace)
    job = store.enqueue(saved['id'], 'live')
    final = {'task_id': trace.task_id, 'task_description': trace.task_description, 'total_steps': len(trace.messages),
             'reward': 0, 'critical_error': {'unified_critical_step': 1, 'unified_taxonomy_tag': 'act.UnsafeOrForbiddenAction',
                                           'llm_pick_rationale': 'Refund without approval.'},
             'step_triggers': [], 'full_instance_states': [], 'chain_summary': {}}
    run_id = uuid.uuid4().hex
    directory = settings.data_dir / 'trajdebug' / 'runs' / run_id
    directory.mkdir(parents=True)
    (directory / 'input.json').write_text(json.dumps(to_unified(trace)))
    (directory / 'input_final.json').write_text(json.dumps(final))
    (directory / 'input_stage_a.json').write_text('{}')
    report = present_report(trace, {'final': final}, model='fixture', usage={}, run_id=run_id)
    report['unified_input_sha256'] = hashlib.sha256((directory / 'input.json').read_bytes()).hexdigest()
    store.update(job['id'], status='completed', stage='诊断完成', progress=100, result=report)
    return store.job(job['id']), directory


def wait_feedback(client, job_id):
    for _ in range(200):
        feedback = client.get(f'/api/jobs/{job_id}').json()['feedback']
        if feedback and feedback['status'] in {'completed', 'failed'}:
            return feedback
        time.sleep(.025)
    raise AssertionError('Feedback did not finish')


@pytest.mark.parametrize('otel', [False, True])
async def test_official_feedback_uses_original_artifacts_and_one_model_call(tmp_path, provider, otel):
    url, calls, state = provider
    hint = HINT + ' 退款前请确认审批😊'
    state['hint'] = hint
    settings = Settings(data_dir=tmp_path, base_url=url, model='fixture', api_key='SECRET_KEY', max_retries=0, timeout=5)
    store = Store(tmp_path)
    job, source = seed_job(store, settings, otel=otel)
    original = copy.deepcopy(job)
    feedback_id = uuid.uuid4().hex
    result = await generate_feedback(job, settings, feedback_id)
    assert result['hint_sentence'] == hint
    assert result['official_report']['fix_suggestion']['hint_sentence'] == hint
    assert result['critical_step'] == 1 and result['alternatives'][0]['step'] == 3
    assert result['usage']['requests'] == 1 and len(calls) == 1
    assert 'single actionable instruction' in calls[0]['messages'][-1]['content']
    assert calls[0]['response_format'] == {'type': 'json_object'}
    assert calls[0]['temperature'] == 0.0
    assert 'SECRET_GOLD' not in json.dumps(calls) and 'SECRET_TELEMETRY' not in json.dumps(calls)
    assert store.job(job['id']) == original
    snapshot = tmp_path / 'trajdebug' / 'feedback' / feedback_id
    for name in ('input.json', 'input_final.json', 'input_stage_a.json'):
        assert (snapshot / name).read_bytes() == (source / name).read_bytes()
    assert json.loads((snapshot / 'input_report.json').read_text(encoding='utf-8')) == result['official_report']
    assert 'SECRET_KEY' not in (snapshot / 'settings.json').read_text(encoding='utf-8')


def test_frontend_api_queue_review_export_and_restart_are_independent_of_diagnosis(tmp_path, provider):
    url, calls, _ = provider
    settings = Settings(data_dir=tmp_path, base_url=url, model='fixture', api_key='test', max_retries=0)
    app = create_app(settings)
    job, _ = seed_job(app.state.store, settings)
    original = copy.deepcopy(job['result'])
    with TestClient(app) as client:
        review = {'decision': 'modified', 'critical_step': 3, 'category': 'reviewed', 'summary': '人工修改结论', 'notes': 'PRIVATE_REVIEW_NOTE'}
        assert client.post(f"/api/jobs/{job['id']}/reviews", json=review).status_code == 200
        assert client.get(f"/api/jobs/{job['id']}").json()['feedback_eligible'] is True
        first = client.post(f"/api/jobs/{job['id']}/feedback")
        assert first.status_code == 200
        assert client.post(f"/api/jobs/{job['id']}/feedback").json()['id'] == first.json()['id']
        run = wait_feedback(client, job['id'])
        assert run['status'] == 'completed', run
        assert len(calls) == 1 and 'PRIVATE_REVIEW_NOTE' not in json.dumps(calls)
        assert client.get(f"/api/jobs/{job['id']}").json()['result'] == original
        assert run['result']['critical_step'] == 1
        assert client.get('/api/jobs').json()[0]['feedback']['status'] == 'completed'
        assert client.post(f"/api/jobs/{job['id']}/feedback").json()['id'] == run['id']
        assert client.get(f"/api/jobs/{job['id']}/feedback/export").json() == run['result']['official_report']
        exported = client.get(f"/api/jobs/{job['id']}/export?format=json").json()
        assert exported['job']['result'] == original and exported['feedback']['id'] == run['id']
        assert len(exported['feedback_history']) == 1
        markdown = client.get(f"/api/jobs/{job['id']}/export?format=md").text
        assert HINT in markdown and '人工修改结论' in markdown and '模型原始诊断的关键步骤 #1' in markdown
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/jobs/{job['id']}").json()['feedback']['result']['hint_sentence'] == HINT
        assert len(calls) == 1


def test_parse_failure_can_retry_without_rerunning_diagnosis(tmp_path, provider):
    url, calls, state = provider
    state['invalid'] = True
    settings = Settings(data_dir=tmp_path, base_url=url, model='fixture', api_key='test', max_retries=0)
    app = create_app(settings)
    job, _ = seed_job(app.state.store, settings)
    with TestClient(app) as client:
        first = client.post(f"/api/jobs/{job['id']}/feedback").json()
        failed = wait_feedback(client, job['id'])
        assert failed['status'] == 'failed' and failed['result']['usage']['requests'] == 3
        assert client.get(f"/api/jobs/{job['id']}").json()['status'] == 'completed'
        assert client.get(f"/api/jobs/{job['id']}/feedback/export").status_code == 409
        assert (tmp_path / 'trajdebug/feedback' / first['id'] / 'input_report.json').is_file()
        state['invalid'] = False
        second = client.post(f"/api/jobs/{job['id']}/feedback").json()
        assert second['id'] != first['id']
        complete = wait_feedback(client, job['id'])
        assert complete['status'] == 'completed'
        assert complete['result']['usage']['cache_hits'] == 0
        assert complete['result']['usage']['requests'] == 1
        assert app.state.store.job(job['id'])['result'] == job['result']
        assert [r['status'] for r in app.state.store.feedback_history(job['id'])] == ['completed', 'failed']


@pytest.mark.parametrize('problem', ['running', 'demo', 'success', 'no_critical', 'missing', 'input_changed', 'final_changed', 'old_version', 'no_model'])
def test_ineligible_feedback_is_rejected_without_creating_a_task(tmp_path, problem):
    settings = Settings(data_dir=tmp_path, base_url='http://127.0.0.1:9/v1', model='fixture')
    app = create_app(settings)
    job, source = seed_job(app.state.store, settings)
    if problem == 'running':
        app.state.store.update(job['id'], status='running')
    elif problem == 'demo':
        with app.state.store.connect() as db:
            db.execute("UPDATE jobs SET mode='demo' WHERE id=?", (job['id'],))
    elif problem == 'success':
        value = json.loads((source / 'input.json').read_text())
        value['metadata']['reward'] = 1
        (source / 'input.json').write_text(json.dumps(value))
    elif problem == 'no_critical':
        value = json.loads((source / 'input_final.json').read_text())
        value['critical_error']['unified_critical_step'] = None
        (source / 'input_final.json').write_text(json.dumps(value))
    elif problem == 'missing':
        (source / 'input.json').unlink()
    elif problem == 'input_changed':
        (source / 'input.json').write_text((source / 'input.json').read_text() + '\n')
    elif problem == 'final_changed':
        value = json.loads((source / 'input_final.json').read_text())
        value['new_field'] = 'changed'
        (source / 'input_final.json').write_text(json.dumps(value))
    elif problem == 'old_version':
        job['result']['provenance']['commit'] = 'old'
        app.state.store.update(job['id'], result=job['result'])
    elif problem == 'no_model':
        settings.model = ''
    client = TestClient(app)
    assert client.get(f"/api/jobs/{job['id']}").json()['feedback_eligible'] is False
    assert client.post(f"/api/jobs/{job['id']}/feedback").status_code == 409
    assert app.state.store.feedback_history(job['id']) == []


def test_feedback_claim_deduplication_and_restart_recovery(tmp_path):
    settings = Settings(data_dir=tmp_path)
    store = Store(tmp_path)
    job, _ = seed_job(store, settings)
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: store.enqueue_feedback(job['id']), range(8)))
    assert len({r['id'] for r in runs}) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim_feedback(), range(8)))
    assert len([r for r in claims if r]) == 1
    store.recover()
    assert store.latest_feedback(job['id'])['status'] == 'failed'
    assert store.job(job['id'])['status'] == 'completed'


async def test_cancelling_feedback_kills_its_subprocess(tmp_path, provider, monkeypatch):
    url, _, state = provider
    state['slow'] = True
    settings = Settings(data_dir=tmp_path, base_url=url, model='fixture', api_key='test')
    job, _ = seed_job(Store(tmp_path), settings)
    started, processes = asyncio.Event(), []
    original = asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        started.set()
        return process
    monkeypatch.setattr('tracelens.feedback.asyncio.create_subprocess_exec', spawn)
    task = asyncio.create_task(generate_feedback(job, settings, uuid.uuid4().hex))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None
