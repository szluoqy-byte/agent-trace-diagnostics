import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time
from fastapi.testclient import TestClient
from tracelens.config import Settings
from tracelens.demo import demo_trace
from tracelens.main import create_app
from tracelens.queue import JobQueue
from tracelens.store import Store


def wait_complete(client, job_id):
    for _ in range(100):
        job = client.get('/api/jobs/' + job_id).json()
        if job['status'] in {'completed', 'failed'}:
            return job
        time.sleep(0.03)
    raise AssertionError('Job did not finish')


def test_full_demo_review_export_and_restart(tmp_path):
    settings = Settings(data_dir=tmp_path, enable_validation_data=True)
    app = create_app(settings)
    trace_id = app.state.store.add_trace(demo_trace())['id']
    with TestClient(app) as client:
        job_id = client.post('/api/jobs', json={'trace_ids':[trace_id], 'mode':'demo'}).json()['jobs'][0]['id']
        job = wait_complete(client, job_id)
        assert job['status'] == 'completed'
        assert job['result']['mode'] == 'demo'
        original = job['result']['attribution'].copy()
        review = {'decision':'modified','critical_step':7,'category':'重试前未检查授权','summary':'人工修订结论','notes':'已检查授权上下文'}
        assert client.post(f'/api/jobs/{job_id}/reviews', json=review).status_code == 200
        updated = client.get(f'/api/jobs/{job_id}').json()
        assert updated['result']['attribution'] == original
        assert updated['reviews'][0]['data']['critical_step'] == 7
        review['critical_step'] = 1
        assert client.post(f'/api/jobs/{job_id}/reviews', json=review).status_code == 422
        report = client.get(f'/api/jobs/{job_id}/export?format=md')
        assert '人工修订结论' in report.text
        assert '历史合成数据，未运行模型' in report.text
        assert '引用证据' in report.text
        assert client.get(f'/api/jobs/{job_id}/export?format=json').json()['reviews']
        assert client.post(f'/api/jobs/{job_id}/retry').status_code == 409
    with TestClient(create_app(settings)) as client:
        assert client.get(f'/api/jobs/{job_id}').json()['reviews'][0]['data']['summary'] == '人工修订结论'


def test_import_atomic_validation_and_demo_restriction(tmp_path, deepagent_template):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        before = len(client.get('/api/traces').json())
        good = deepagent_template
        response = client.post('/api/import',files=[('files',('good.json',json.dumps(good),'application/json')),('files',('bad.json','broken','application/json'))])
        assert response.status_code == 422
        assert len(client.get('/api/traces').json()) == before
        imported = client.post('/api/import',files={'files':('good.json',json.dumps(good),'application/json')}).json()['trace_ids'][0]
        assert client.post('/api/jobs',json={'trace_ids':[imported],'mode':'demo'}).status_code == 422
        assert client.post('/api/jobs',json={'trace_ids':[imported],'mode':'live'}).status_code == 409


def test_trace_uploads_accept_network_browser_origins(tmp_path, deepagent_template):
    files = {'files': ('trace.json', json.dumps(deepagent_template), 'application/json')}
    routes = {'/api/import/preview': 'rows', '/api/import': 'trace_ids'}
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        for route, key in routes.items():
            for origin in ('http://127.0.0.1:8017', 'http://192.0.2.20:8017', 'https://traces.example'):
                upload = client.post(route, files=files, headers={'Origin': origin})
                assert upload.status_code == 200 and upload.json()[key]


def test_atomic_queue_claim_and_duplicate_enqueue(tmp_path):
    store = Store(tmp_path)
    trace_id = store.add_trace(demo_trace())['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: store.enqueue(trace_id, 'demo'), range(8)))
    assert len({j['id'] for j in jobs}) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(8)))
    assert len([c for c in claims if c]) == 1
    store.recover()
    assert store.job(jobs[0]['id'])['status'] == 'failed'


def test_manual_retry_preserves_failed_artifacts(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, enable_validation_data=True))
    trace_id = app.state.store.add_trace(demo_trace())['id']
    with TestClient(app) as client:
        store = app.state.store
        old = store.enqueue(trace_id, 'demo')
        store.update(old['id'],status='failed',error='interrupted',result={'partial':'retained'})
        response = client.post(f"/api/jobs/{old['id']}/retry")
        assert response.status_code == 200
        assert response.json()['id'] != old['id']
        assert store.job(old['id'])['result'] == {'partial':'retained'}
        assert store.job(old['id'])['status'] == 'failed'


async def test_queue_limits_concurrency_and_persists_failure(tmp_path, monkeypatch):
    store = Store(tmp_path)
    active, peak = 0, 0
    async def fake_diagnose(trace, settings, progress):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await progress('testing', 50, {})
        await asyncio.sleep(.05)
        active -= 1
        if trace.task_id == '2':
            raise ValueError('expected diagnostic failure')
        return {'mode':'live'}
    monkeypatch.setattr('tracelens.queue.diagnose',fake_diagnose)
    for i in range(5):
        trace=demo_trace(); trace.task_id=str(i); trace.kind='imported'
        store.enqueue(store.add_trace(trace)['id'], 'live')
    queue = JobQueue(store, Settings(data_dir=tmp_path, concurrency=2))
    await queue.start()
    for _ in range(100):
        if all(j['status'] in {'completed','failed'} for j in store.jobs()):
            break
        await asyncio.sleep(.02)
    await queue.stop()
    assert peak == 2
    assert sum(j['status'] == 'completed' for j in store.jobs()) == 4
    assert sum(j['status'] == 'failed' for j in store.jobs()) == 1
