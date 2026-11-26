import json
from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient

from test_deepagent import completion, export, span
from test_trajdebug import provider
from tracelens.agent_identity import AgentOwnership
from tracelens.config import Settings
from tracelens.main import create_app
from tracelens.normalize import fingerprint, parse_upload
from tracelens.store import Store


TEMPLATES = Path(__file__).parents[2] / 'frontend' / 'public' / 'templates'


def delegated_trace():
    payload = export([
        {**span('api', 'API', 0, 12, response={'status': 'failed'}), 'service_name': 'gateway'},
        {**span('dispatcher', 'AGENT', 1, 11, parent='api', request={'message': 'Check the order'},
                 response={'info': {'finish': 'error'}}), 'service_name': 'dispatcher'},
        {**span('approval', 'AGENT', 2, 10, parent='dispatcher', response={'info': {'finish': 'error'}}),
         'service_name': 'approval-agent'},
        {**span('llm', 'LLM', 3, 7, parent='approval', response=completion('Skipped approval', 'stop')),
         'service_name': 'model-gateway'},
    ])
    [trace] = parse_upload(json.dumps(payload).encode(), 'delegated.json')
    trace.outcome = 'failed'
    return trace


def otel_trace():
    [trace] = parse_upload((TEMPLATES / 'otel-genai-template.json').read_bytes(), 'refund.json')
    trace.outcome = 'failed'
    return trace


def test_span_ownership_and_agent_backfill_keep_diagnosis_identity(tmp_path):
    trace = delegated_trace()
    assert trace.agent_identity.name == 'dispatcher'
    assert AgentOwnership(trace.model_dump()).for_message(trace.messages[0].model_dump()).name == 'approval-agent'
    assert otel_trace().agent_identity.name == 'refund-assistant'

    old_hash, old_payload = fingerprint(trace), trace.input_json()
    store = Store(tmp_path)
    trace_id = store.add_trace(trace)['id']
    with store.connect() as db:
        db.execute("UPDATE traces SET data=json_remove(data,'$.agent_identity') WHERE id=?", (trace_id,))
        db.execute("DELETE FROM workspace_meta WHERE key='trajectory_agent_identity_v1'")
    store = Store(tmp_path)
    saved = store.trace(trace_id)
    assert saved['data']['agent_identity']['name'] == 'dispatcher' and saved['hash'] == old_hash
    assert saved['data']['agent_identity']['span_id'] == 'dispatcher'
    assert trace.input_json() == old_payload
    assert store.trace_library(1, 10, 'dispatcher', 'all', 'all', 'all')['items'][0]['agent_name'] == 'dispatcher'


def test_task_suggestions_use_verified_multi_engine_evidence_and_retry_per_agent(tmp_path, provider):
    endpoint, requests, state = provider
    app = create_app(Settings(data_dir=tmp_path, base_url=endpoint, api_key='fixture-key',
                              model='fixture-model', headers={'X-Tenant': 'fixture'}, max_retries=0))
    client = TestClient(app)
    store = app.state.store
    first, second = delegated_trace(), otel_trace()
    pending = otel_trace().model_copy(update={'task_id': 'pending-review'})
    traces = [first, second, pending]
    trace_ids = [store.add_trace(trace)['id'] for trace in traces]
    created = client.post('/api/tasks', json={'name': 'Agent improvements', 'trace_ids': trace_ids, 'start': True,
                                               'goal': 'Check approval', 'prompt_language': 'zh'})
    assert created.status_code == 200, created.text
    task_id, run_id = (created.json()[key] for key in ('task_id', 'run_id'))
    assert [row['agent_name'] for row in store.run_rows(run_id)] == ['dispatcher', 'refund-assistant', 'refund-assistant']
    for index, (row, trace) in enumerate(zip(store.run_rows(run_id), traces, strict=True)):
        step = 0 if index == 0 else 1
        quote = 'Skipped approval' if index == 0 else trace.messages[step].content
        report = {'schema_version': 2, 'algorithm': 'AgentDebugX', 'attribution': {'critical_step': step, 'summary': '失效的审批检查'},
                  'instances': [], 'evidence': [{'step': step, 'quote': quote, 'original_matched': True}]}
        if index:
            report['comparison'] = {'state': 'aligned' if index == 1 else 'different', 'primary': 'agentdebugx'}
        store.update(row['job_id'], status='completed', result=report)

    allow_refund = False

    def answer(system, user):
        assert system.startswith('为当前 Agent')
        prompt = json.loads(user)
        evidence_id = 'E99' if prompt['agent'] == 'refund-assistant' and not allow_refund else 'E1'
        return {'suggestions': [{'priority': 'high', 'problem': '审批缺失', 'change': '增加审批检查',
                                 'verification': '重放缺少审批的请求', 'evidence_ids': [evidence_id]}]}

    state['extra_request_reply'] = answer
    route = f'/api/tasks/{task_id}/runs/{run_id}/improvements'

    def wait_for_report(client, status):
        deadline = monotonic() + 10
        while True:
            report = client.get(route).json()['report']
            if report['status'] not in {'queued', 'running'} or monotonic() >= deadline:
                assert report['status'] == status, report
                return report
            sleep(0.05)

    with TestClient(app) as client:
        submitted = client.post(route)
        assert submitted.status_code == 200, submitted.text
        report = wait_for_report(client, 'partial')
        assert report['snapshot']['included'] == 2 and report['snapshot']['exclusions'] == {'pending_review': 1}
        assert {group['agent']['name'] for group in report['snapshot']['agents']} == {'approval-agent', 'refund-assistant'}
        assert len(requests) == 3
        allow_refund = True
        assert client.post(route).status_code == 200
        regenerated = wait_for_report(client, 'completed')
        assert len(requests) == 4
        assert all(call['request']['model'] == 'fixture-model' and call['headers']['x-tenant'] == 'fixture' for call in requests)
        assert all(result['suggestions'][0]['trace_count'] == 1 for result in regenerated['results'].values())
        assert regenerated['id'] != report['id'] and regenerated['snapshot'] == report['snapshot']
        assert client.get(route, params={'report_id': report['id']}).json()['report']['status'] == 'partial'
        assert client.delete('/api/tasks/' + task_id).status_code == 200
