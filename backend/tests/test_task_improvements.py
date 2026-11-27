import json
from pathlib import Path
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from test_deepagent import completion, export, span
from test_trajdebug import provider
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


def wait_for_report(client, route, status):
    deadline = monotonic() + 10
    while True:
        report = client.get(route).json()['report']
        if report['status'] not in {'queued', 'running'} or monotonic() >= deadline:
            assert report['status'] == status, (report['error'], report['results'])
            return report
        sleep(0.05)


def test_span_ownership_and_agent_backfill_keep_diagnosis_identity(tmp_path):
    trace = delegated_trace()
    assert trace.agent_identity.name == 'dispatcher'
    assert trace.messages[0].source_step == 'llm:span'
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
        if index == 1:
            report['engine_runs'] = {'agentdebugx': {'status': 'completed'}, 'chief': {
                'status': 'completed', 'report': {'attribution': {'critical_step': step}},
            }}
            report['synthesis'] = {'status': 'completed', 'summary': '综合结论',
                                   'evidence': report.pop('evidence')}
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

    with TestClient(app) as client:
        submitted = client.post(route)
        assert submitted.status_code == 200, submitted.text
        report = wait_for_report(client, route, 'partial')
        assert report['snapshot']['included'] == 2 and report['snapshot']['exclusions'] == {'pending_review': 1}
        assert {group['agent']['name'] for group in report['snapshot']['agents']} == {'dispatcher', 'refund-assistant'}
        refund = next(group for group in report['snapshot']['agents'] if group['agent']['name'] == 'refund-assistant')
        assert '引用了未提供的证据' in report['results'][refund['key']]['error']
        assert len(requests) == 3
        assert '上次包含无效引用' in requests[-1]['request']['messages'][0]['content']
        allow_refund = True
        assert client.post(route).status_code == 200
        regenerated = wait_for_report(client, route, 'completed')
        assert len(requests) == 4
        assert all(call['request']['model'] == 'fixture-model' and call['headers']['x-tenant'] == 'fixture' for call in requests)
        assert all(result['suggestions'][0]['trace_count'] == 1 for result in regenerated['results'].values())
        assert regenerated['id'] != report['id'] and regenerated['snapshot'] == report['snapshot']
        assert client.get(route, params={'report_id': report['id']}).json()['report']['status'] == 'partial'
        assert client.delete('/api/tasks/' + task_id).status_code == 200


@pytest.mark.parametrize('supports_json_mode', [True, False])
def test_task_suggestions_resume_after_truncation_with_all_ten_evidence_refs(tmp_path, provider, supports_json_mode):
    endpoint, requests, state = provider
    app = create_app(Settings(data_dir=tmp_path, base_url=endpoint, api_key='fixture-key',
                              model='fixture-model', max_tokens=6000, max_retries=0))
    store = app.state.store
    traces = []
    for sequence in range(10):
        payload = export([
            span('api', 'API', 0, 5, response={'error': f'Gateway failure {sequence}'}),
            {**span('agent', 'AGENT', 1, 4, parent='api', response={'info': {'finish': 'error'}}),
             'service_name': 'approval-agent'},
        ])
        [trace] = parse_upload(json.dumps(payload).encode(), f'gateway-{sequence}.json')
        trace.outcome = 'failed'
        traces.append(trace)
    ids = [store.add_trace(trace)['id'] for trace in traces]
    client = TestClient(app)
    submission = client.post('/api/tasks', json={'name': 'Gateway failures', 'trace_ids': ids, 'start': True,
                                                  'prompt_language': 'zh'})
    assert submission.status_code == 200, submission.text
    task_id, run_id = (submission.json()[key] for key in ('task_id', 'run_id'))
    for row, trace in zip(store.run_rows(run_id), traces, strict=True):
        assert row['agent_name'] == 'approval-agent'
        step = next(i for i, message in enumerate(trace.messages) if message.source_step == 'api:span')
        report = {'schema_version': 2, 'algorithm': 'AgentDebugX',
                  'attribution': {'critical_step': step, 'summary': 'Gateway failure'},
                  'evidence': [{'step': step, 'quote': trace.messages[step].content, 'original_matched': True}]}
        store.update(row['job_id'], status='completed', result=report)

    evidence_ids = [f'E{index}' for index in range(1, 11)]
    answer = {'suggestions': [{'priority': 'high', 'problem': '调用失败', 'change': '处理网关异常',
                               'verification': '重放失败调用', 'evidence_ids': evidence_ids}]}
    state['extra_request_reply'] = lambda _system, _user: '建议如下\n```json\n' + json.dumps(answer) + '\n```'
    state['reject_json_mode'] = not supports_json_mode

    def truncate_first_completion(response):
        if len(requests) == (1 if supports_json_mode else 2):
            response['choices'][0]['finish_reason'] = 'length'
            response['choices'][0]['message']['content'] = '{"suggestions": ['

    state['override_response'] = truncate_first_completion
    route = f'/api/tasks/{task_id}/runs/{run_id}/improvements'
    with TestClient(app) as client:
        assert client.post(route).status_code == 200
        report = wait_for_report(client, route, 'completed')

    assert report['snapshot']['included'] == 10
    assert report['snapshot']['exclusions'] == {}
    [group] = report['snapshot']['agents']
    assert [sample['id'] for sample in group['samples']] == evidence_ids
    [suggestion] = report['results'][group['key']]['suggestions']
    assert suggestion['evidence_ids'] == evidence_ids and suggestion['trace_count'] == 10
    sent = [call['request'] for call in requests]
    assert sent[0]['response_format'] == {'type': 'json_object'}
    assert [call['max_tokens'] for call in sent] == ([4096, 6000] if supports_json_mode else [4096, 4096, 6000])
    assert ('response_format' in sent[-1]) is supports_json_mode
    assert '上次输出被截断' in sent[-1]['messages'][0]['content']
    assert ', '.join(evidence_ids) in sent[-1]['messages'][0]['content']
    assert report['results'][group['key']]['usage'] == {
        'requests': len(requests), 'prompt_tokens': 20, 'completion_tokens': 10,
    }
