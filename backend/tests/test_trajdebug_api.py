import copy
import json
from time import monotonic, sleep
from fastapi.testclient import TestClient
from test_trajdebug import pipeline_trace, provider
from tracelens.config import Settings
from tracelens.demo import demo_trace, demo_report
from tracelens.main import create_app
from tracelens.normalize import parse_upload


def test_import_requires_an_explicit_outcome_before_native_diagnosis(tmp_path, deepagent_template):
    app = create_app(Settings(data_dir=tmp_path, base_url='http://127.0.0.1:9/v1', model='fixture'))
    data = deepagent_template
    with TestClient(app) as client:
        response = client.post('/api/import', files={'files':('x.json',json.dumps(data),'application/json')})
        trace_id = response.json()['trace_ids'][0]
        assert client.post('/api/jobs', json={'trace_ids':[trace_id],'mode':'live'}).status_code == 422
        response = client.post('/api/import', files={'files':('x.json',json.dumps(data),'application/json')}, data={'outcome':'failed'})
        trace_id = response.json()['trace_ids'][0]
        assert client.get('/api/traces/'+trace_id).json()['data']['outcome'] == 'failed'
        assert client.get('/api/config').json()['algorithm'] == 'TrajDebug'
        assert 'api_key' not in client.get('/api/config').json()
        data['outcome'] = 'succeeded'
        response = client.post('/api/import', files={'files':('ok.json',json.dumps(data),'application/json')}, data={'outcome':'failed'})
        assert client.get('/api/traces/'+response.json()['trace_ids'][0]).json()['data']['outcome'] == 'succeeded'


def test_review_and_native_artifact_exports_keep_original_output(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    store = app.state.store
    trace = store.add_trace(demo_trace())
    job = store.enqueue(trace['id'], 'demo')
    report = demo_report()
    original = copy.deepcopy(report)
    report['provenance']['run_id'] = 'a'*32
    store.update(job['id'], status='completed', progress=100, result=report)
    directory = tmp_path/'trajdebug/runs'/('a'*32)
    directory.mkdir(parents=True)
    (directory/'input_final.json').write_text(json.dumps(original['official_output']))
    with TestClient(app) as client:
        review = {'decision':'modified','critical_step':7,'category':'reviewed','summary':'人工修改结论','notes':'补充修复验证计划'}
        assert client.post('/api/jobs/'+job['id']+'/reviews',json=review).status_code == 200
        after = client.get('/api/jobs/'+job['id']).json()
        assert after['result']['official_output'] == original['official_output']
        assert after['result']['attribution'] == original['attribution']
        assert client.get('/api/jobs/'+job['id']+'/artifacts/final').json() == original['official_output']
        assert client.get('/api/jobs/'+job['id']+'/artifacts/settings').status_code == 404
        md = client.get('/api/jobs/'+job['id']+'/export?format=md').text
        assert '人工修改结论' in md and '补充修复验证计划' in md
        assert '错误生命周期' in md and '约束检查' not in md
        assert client.get('/api/jobs/'+job['id']+'/export?format=json').json()['job']['result']['official_output'] == original['official_output']
        review.update(decision='rejected',critical_step=7)
        assert client.post('/api/jobs/'+job['id']+'/reviews',json=review).status_code == 422


def test_trajectory_templates_are_downloadable(tmp_path):
    frontend = tmp_path / 'frontend'
    (frontend / 'assets').mkdir(parents=True)
    (frontend / 'index.html').touch()
    app = create_app(Settings(data_dir=tmp_path / 'data'), frontend_dir=frontend)
    with TestClient(app) as client:
        for filename, count in [
            ('deepagent-template.json', 1),
            ('otel-genai-template.json', 1),
            ('otel-genai-batch-template.json', 2),
        ]:
            response = client.get('/templates/' + filename)
            assert response.status_code == 200
            assert len(parse_upload(response.content, filename, workbench=True)) == count
        assert client.get('/templates/trajectory-json-template.json').status_code == 404
        assert client.get('/templates/trajectory-jsonl-template.jsonl').status_code == 404


def test_regeneration_calls_model_again_and_preserves_previous_attempt(tmp_path, provider):
    url, calls, _ = provider
    app = create_app(Settings(data_dir=tmp_path, base_url=url, model='fixture', max_retries=0))
    trace_id = app.state.store.add_trace(pipeline_trace())['id']

    def completed(client, job_id):
        until = monotonic() + 10
        while True:
            job = client.get(f'/api/jobs/{job_id}').json()
            if job['status'] in {'completed', 'failed'} or monotonic() >= until:
                assert job['status'] == 'completed', job.get('error')
                return job
            sleep(0.05)

    with TestClient(app) as client:
        task = client.post('/api/tasks', json={'name':'重新生成验收', 'trace_ids':[trace_id], 'start':True}).json()
        path = f"/api/tasks/{task['task_id']}/runs/{task['run_id']}/traces/{trace_id}"
        original_id = client.get(path).json()['job']['id']
        original = completed(client, original_id)
        assert original['result']['usage']['requests'] > 0
        app.state.store.add_review(original_id, {'decision':'modified', 'summary':'既有复核'})
        request_count = len(calls)
        regenerate_path = f'/api/jobs/{original_id}/regenerate'
        response = client.post(regenerate_path)
        assert response.status_code == 200, response.text
        new_id = response.json()['id']
        assert client.post(regenerate_path).status_code == 409
        second = completed(client, new_id)
        assert len(calls) == request_count * 2
        assert second['result']['usage']['cache_hits'] == 0
        assert second['reviews'] == []
        detail = client.get(path).json()
        assert detail['job']['id'] == new_id
        assert [row['id'] for row in detail['attempts']] == [new_id, original_id]
        old = client.get(f'/api/jobs/{original_id}').json()
        assert old['result'] == original['result'] and old['read_only']
        assert old['reviews'][0]['data']['summary'] == '既有复核'
