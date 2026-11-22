import copy
import json
from fastapi.testclient import TestClient
from tracelens.config import Settings
from tracelens.demo import demo_trace, demo_report
from tracelens.main import create_app


def test_import_requires_an_explicit_outcome_before_native_diagnosis(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, base_url='http://127.0.0.1:9/v1', model='fixture'))
    data = {'messages': [{'role':'user','content':'task'}, {'role':'assistant','content':'answer'}]}
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
