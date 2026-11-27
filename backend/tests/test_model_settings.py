import asyncio
import sqlite3
import stat

import httpx
from fastapi.testclient import TestClient

from tracelens.config import Settings
from tracelens.execution import job_settings
from tracelens.main import create_app
from tracelens.model_concurrency import ModelSlots
from tracelens.normalize import normalize
from tracelens.queue import JobQueue
from tracelens.store import Store


PATH = '/api/model-configurations'


def test_model_slots_retries_extended_sqlite_busy_code(tmp_path, monkeypatch):
    slots = ModelSlots(tmp_path, 1)

    def recovering():
        exc = sqlite3.OperationalError('database is locked')
        exc.sqlite_errorcode = sqlite3.SQLITE_BUSY_RECOVERY
        raise exc

    with monkeypatch.context() as patch:
        patch.setattr(slots, 'limit', recovering)
        assert slots.try_acquire() is None
    with slots.request():
        assert slots.try_acquire() is None


def test_default_switch_freezes_each_run_and_keeps_credentials_private(tmp_path):
    settings = Settings(data_dir=tmp_path, base_url='https://environment.example/v1',
                        model='environment-model', api_key='environment-secret')
    app = create_app(settings)
    store = app.state.store
    client = TestClient(app)
    assert client.get(PATH).json()['items'][0]['is_default']

    def create_model(name, *, default):
        response = client.post(PATH, json={'name': name, 'model': name + '-model',
                                           'base_url': f'https://{name}.example/v1/chat/completions',
                                           'api_key': name + '-private-key', 'make_default': default})
        assert response.status_code == 201
        return response.json()['id']

    first = create_model('first', default=True)
    second = create_model('second', default=False)
    assert client.get('/api/config').json()['model'] == 'first-model'
    listed = client.get(PATH)
    assert all(secret not in listed.text for secret in ('first-private-key', 'second-private-key', 'environment-secret'))
    assert next(item for item in listed.json()['items'] if item['id'] == first)['base_url'] == 'https://first.example/v1'
    assert stat.S_IMODE(store.model_connections.path.stat().st_mode) == 0o600

    trace_id = store.add_trace(normalize({'outcome': 'failed', 'messages': [{'role': 'assistant', 'content': 'done'}]}))['id']

    def start(name):
        response = client.post('/api/tasks', json={'name': name, 'trace_ids': [trace_id], 'start': True})
        assert response.status_code == 200, response.text
        task = response.json()
        job_id = store.run_rows(task['run_id'])[0]['job_id']
        return task, job_id

    task, first_job = start('Original analysis')
    assert client.post(PATH + '/unknown/default').status_code == 404
    assert client.get('/api/config').json()['model'] == 'first-model'
    assert client.post(f'{PATH}/{second}/default').status_code == 200
    _, second_job = start('New analysis')
    assert job_settings(store, settings, first_job).api_key == 'first-private-key'
    assert job_settings(store, settings, second_job).base_url == 'https://second.example/v1'
    assert store.job_config(first_job)['model_connection_id'] == first
    assert 'api_key' not in store.job_config(first_job)
    detail_path = f"/api/tasks/{task['task_id']}/runs/{task['run_id']}"
    assert 'first-private-key' not in client.get(detail_path + '/export').text
    store.update(first_job, status='failed')
    retry = client.post(detail_path + '/actions', json={'action': 'retry_failed'})
    assert retry.status_code == 200
    retry_job, = retry.json()['job_ids']
    assert job_settings(store, settings, retry_job).api_key == 'first-private-key'

    restored = TestClient(create_app(settings))
    assert restored.get('/api/config').json()['model'] == 'second-model'
    assert restored.post(PATH + '/environment/default').status_code == 200
    assert restored.get('/api/config').json()['model'] == 'environment-model'


def test_task_can_select_a_diagnosis_model_and_keep_it_in_a_draft(tmp_path):
    settings = Settings(data_dir=tmp_path, base_url='https://environment.example/v1',
                        model='environment-model', api_key='environment-secret')
    app = create_app(settings)
    store = app.state.store
    client = TestClient(app)
    default = store.model_connections.add(name='Default', model='default-model',
                                          base_url='https://default.example/v1', api_key='', make_default=True,
                                          runtime={'concurrency': 3, 'llm_concurrency': 4})
    alternate = store.model_connections.add(name='Alternate', model='alternate-model',
                                            base_url='https://alternate.example/v1', api_key='alternate-secret',
                                            headers={'X-Tenant': 'alternate'}, runtime={'timeout': 42, 'max_tokens': 6000})
    playground = store.model_connections.add(name='Playground', model='playground-model',
                                             base_url='https://playground.example/v1', api_key='', purpose='playground')
    trace_id = store.add_trace(normalize({'outcome': 'failed', 'messages': [{'role': 'assistant', 'content': 'done'}]}))['id']
    task_fields = {'name': 'Task with a selected model', 'trace_ids': [trace_id]}
    assert client.post('/api/tasks', json={**task_fields, 'model_connection_id': playground}).status_code == 409

    started = client.post('/api/tasks', json={**task_fields, 'model_connection_id': alternate, 'start': True})
    assert started.status_code == 200, started.text
    draft = client.post('/api/tasks', json={**task_fields, 'model_connection_id': alternate})
    assert draft.status_code == 200, draft.text
    task_path = '/api/tasks/' + draft.json()['task_id']
    assert client.get(task_path).json()['model_connection_id'] == alternate

    launched = client.post(task_path + '/runs', json={})
    assert launched.status_code == 200, launched.text
    for response in (started, launched):
        job_id = store.run_rows(response.json()['run_id'])[0]['job_id']
        actual = job_settings(store, settings, job_id)
        assert actual.model == 'alternate-model' and actual.api_key == 'alternate-secret'
        assert actual.headers == {'X-Tenant': 'alternate'}
        assert actual.timeout == 42 and actual.max_tokens == 6000
        assert (actual.concurrency, actual.llm_concurrency) == (3, 4)
        assert store.job_config(job_id)['model_connection_id'] == alternate

    store.update(job_id, status='completed')
    rerun = client.post(task_path + '/runs', json={'model_connection_id': 'environment'})
    assert rerun.status_code == 200, rerun.text
    env_job = store.run_rows(rerun.json()['run_id'])[0]['job_id']
    assert job_settings(store, settings, env_job).api_key == 'environment-secret'
    assert client.get(task_path).json()['model_connection_id'] == 'environment'
    assert store.model_connections.default()['id'] == default
    assert client.get('/api/config').json()['model'] == 'default-model'


def test_connection_probe_and_validation_do_not_return_secrets(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path, base_url='', model='', api_key=''))
    client = TestClient(app)
    original_client = httpx.AsyncClient
    calls = []

    def provider(request):
        calls.append(request)
        if request.headers['Authorization'] != 'Bearer probe-private-key' or request.headers.get('X-Tenant') != 'tenant-a':
            return httpx.Response(401, json={'error': 'provider returned secret text'})
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok": true}'}}]})

    def use_mock_transport(**kwargs):
        return original_client(transport=httpx.MockTransport(provider), **kwargs)

    monkeypatch.setattr('tracelens.model_settings_api.httpx.AsyncClient', use_mock_transport)
    body = {'name': 'Provider', 'model': 'provider-model', 'base_url': 'http://127.0.0.1:9000/v1',
            'api_key': 'probe-private-key'}
    assert client.post(PATH + '/test', json={**body, 'headers': {'X-Tenant': 'tenant-a'}}).json() == {'ok': True}
    assert str(calls[0].url) == body['base_url'] + '/chat/completions'
    assert client.post(PATH + '/test', json={**body, 'api_key': 'wrong-secret', 'headers': {
        'X-Tenant': 'tenant-a', 'authorization': 'Bearer probe-private-key',
    }}).json() == {'ok': True}
    assert calls[-1].headers.get_list('authorization') == ['Bearer probe-private-key']
    assert client.get(PATH).json()['items'] == []
    config_id = client.post(PATH, json={**body, 'headers': {'X-Tenant': 'tenant-a'}, 'make_default': True}).json()['id']
    assert client.post(f'{PATH}/{config_id}/test').json() == {'ok': True}
    assert app.state.store.model_connections.get(config_id)['headers'] == {'X-Tenant': 'tenant-a'}
    assert 'tenant-a' not in client.get(PATH).text

    detail = client.get(f'{PATH}/{config_id}')
    assert detail.status_code == 200 and detail.json()['model'] == 'provider-model'
    updated = client.put(f'{PATH}/{config_id}', json={
        'name': 'Provider updated', 'model': 'provider-model-v2',
        'base_url': 'http://127.0.0.1:9001/v1', 'headers': {'X-Tenant': 'tenant-b'},
        'make_default': True,
    })
    assert updated.status_code == 200
    saved = app.state.store.model_connections.get(config_id)
    assert saved['model'] == 'provider-model-v2' and saved['api_key'] == 'probe-private-key'
    assert saved['headers'] == {'X-Tenant': 'tenant-b'}
    assert client.delete(f'{PATH}/{config_id}').json() == {'ok': True}
    assert client.get(f'{PATH}/{config_id}').status_code == 404

    failed = client.post(PATH + '/test', json={**body, 'api_key': 'wrong-secret'})
    assert failed.status_code == 502
    assert '认证失败' in failed.json()['detail']
    assert 'secret' not in failed.text
    invalid = client.post(PATH, json={**body, 'base_url': 'https://user:password@example.test/v1?key=secret'})
    assert invalid.status_code == 422
    assert 'password' not in invalid.text and 'secret' not in invalid.text
    missing_name = client.post(PATH, json={k: v for k, v in body.items() if k != 'name'})
    assert missing_name.status_code == 422
    assert body['api_key'] not in missing_name.text
    assert client.post(PATH, json=body, headers={'Origin': 'https://elsewhere.test'}).status_code == 403


def test_runtime_settings_are_editable_and_persisted(tmp_path):
    settings = Settings(data_dir=tmp_path, base_url='https://environment.example/v1', model='environment-model')
    client = TestClient(create_app(settings))
    body = {'concurrency': 4, 'max_retries': 1, 'timeout': 30, 'llm_concurrency': 5,
            'max_tokens': 4096, 'cluster_json_mode': True}
    response = client.put('/api/runtime-settings', json=body)
    assert response.status_code == 200 and response.json() == body
    assert {key: client.get('/api/config').json()[key] for key in body} == body

    restored = TestClient(create_app(Settings(data_dir=tmp_path, base_url='https://environment.example/v1', model='environment-model')))
    assert {key: restored.get('/api/config').json()[key] for key in body} == body
    invalid = restored.put('/api/runtime-settings', json={**body, 'timeout': 3})
    assert invalid.status_code == 422


def test_runtime_settings_fill_backend_defaults_when_fields_are_omitted(tmp_path):
    settings = Settings(data_dir=tmp_path)
    client = TestClient(create_app(settings))
    response = client.put('/api/runtime-settings', json={})
    assert response.status_code == 200
    assert response.json() == {key: getattr(settings, key) for key in (
        'concurrency', 'max_retries', 'timeout', 'llm_concurrency', 'max_tokens', 'cluster_json_mode')}


def test_model_purposes_have_independent_defaults_and_diagnosis_runtime(tmp_path):
    settings = Settings(data_dir=tmp_path, base_url='https://environment.example/v1', model='environment',
                        concurrency=2, llm_concurrency=8, timeout=120, max_retries=2)
    app = create_app(settings)
    store = app.state.store
    client = TestClient(app)
    base = {'name': 'Provider', 'base_url': 'https://provider.example/v1', 'model': 'diagnosis-a',
            'api_key': 'diagnosis-key', 'headers': {'X-Tenant': 'diagnosis-tenant'}, 'make_default': True}
    a = client.post(PATH, json={**base, 'runtime': {'timeout': 45, 'concurrency': 3, 'llm_concurrency': 4}}).json()['id']
    b = client.post(PATH, json={**base, 'model': 'diagnosis-b', 'make_default': False,
                               'runtime': {'max_retries': 0, 'timeout': 95, 'llm_concurrency': 2}}).json()['id']
    play = client.post(PATH, params={'purpose': 'playground'},
                       json={**base, 'model': 'replay', 'api_key': 'playground-key'}).json()['id']
    assert client.get('/api/config').json()['model'] == 'diagnosis-a'
    assert client.get('/api/config').json()['concurrency'] == 3
    assert ModelSlots.for_settings(settings).limit() == 4
    assert {item['id'] for item in client.get(PATH).json()['items']} == {'environment', a, b}
    playground = client.get(PATH, params={'purpose': 'playground'}).json()['items']
    assert [item['id'] for item in playground] == [play]
    assert playground[0]['is_default'] is True and 'runtime' not in playground[0]
    assert client.get(f'{PATH}/{play}').status_code == 404
    assert client.delete(f'{PATH}/{a}', params={'purpose': 'playground'}).status_code == 404
    assert client.post(f'{PATH}/{play}/default').status_code == 404
    assert client.put(f'{PATH}/{play}', params={'purpose': 'playground'},
                      json={**base, 'runtime': {'timeout': 25}}).status_code == 422

    trace_id = store.add_trace(normalize({'outcome': 'failed', 'messages': [{'role': 'assistant', 'content': 'done'}]}))['id']
    task = client.post('/api/tasks', json={'name': 'Frozen', 'trace_ids': [trace_id], 'start': True}).json()
    job_id = store.run_rows(task['run_id'])[0]['job_id']
    assert store.job_config(job_id)['timeout'] == 45
    assert client.post(f'{PATH}/{b}/default').status_code == 200
    assert ModelSlots.for_settings(settings).limit() == 2
    assert client.get('/api/config').json()['timeout'] == 95
    assert client.get('/api/config').json()['max_retries'] == 0
    assert job_settings(store, settings, job_id).timeout == 45
    assert job_settings(store, settings, job_id).llm_concurrency == 2
    assert job_settings(store, settings, job_id).api_key == 'diagnosis-key'
    body = {**base, 'model': 'diagnosis-b', 'runtime': {'timeout': None}}
    assert client.put(f'{PATH}/{b}', json=body).status_code == 200
    assert client.get('/api/config').json()['timeout'] == 120
    assert client.get('/api/config').json()['max_retries'] == 0
    assert client.put('/api/runtime-settings?scope=environment', json={'llm_concurrency': 5}).status_code == 200
    assert ModelSlots.for_settings(settings).limit() == 2
    assert client.post(f'{PATH}/environment/default').status_code == 200
    assert ModelSlots.for_settings(settings).limit() == 5
    assert client.delete(f'{PATH}/{play}', params={'purpose': 'playground'}).status_code == 200


def test_legacy_connections_inherit_saved_runtime_on_upgrade(tmp_path):
    store = Store(tmp_path)
    store.save_runtime_settings({'timeout': 75, 'llm_concurrency': 3, 'concurrency': 4})
    with store.model_connections.connect() as db:
        db.execute('DROP TABLE model_connections')
        db.executescript("""
            CREATE TABLE model_connections (
                id TEXT PRIMARY KEY, name TEXT, base_url TEXT, model TEXT, api_key TEXT, is_default INTEGER
            );
            CREATE UNIQUE INDEX one_default_model_connection ON model_connections(is_default) WHERE is_default=1;
            INSERT INTO model_connections VALUES
                ('original', 'Original', 'https://old.example/v1', 'old-model', 'private-key', 1);
        """)
    settings = Settings(data_dir=tmp_path, base_url='', model='')
    assert ModelSlots.for_settings(settings).limit() == 3
    app = create_app(settings)
    client = TestClient(app)
    [saved] = client.get(PATH).json()['items']
    assert saved['id'] == 'original' and saved['is_default'] and saved['has_key']
    assert saved['runtime']['timeout'] == 75 and saved['runtime']['concurrency'] == 4
    assert ModelSlots.for_settings(settings).limit() == 3
    assert client.get(PATH, params={'purpose': 'playground'}).json() == {'items': []}
    assert client.put('/api/runtime-settings?scope=environment', json={'timeout': 25}).status_code == 200
    restored = TestClient(create_app(Settings(data_dir=tmp_path, base_url='', model='')))
    assert restored.get('/api/config').json()['timeout'] == 75
    assert restored.get('/api/runtime-settings?scope=environment').json()['timeout'] == 25


async def test_lowering_diagnosis_workers_waits_for_running_work(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.model_connections.add(name='Diagnosis', base_url='https://provider.example/v1', model='diagnosis',
                                api_key='', make_default=True, runtime={'concurrency': 3})
    queue = JobQueue(store, Settings(data_dir=tmp_path))
    work = iter(range(1, 5))
    started = asyncio.Queue()
    release = {job: asyncio.Event() for job in range(1, 5)}

    async def handle(job):
        started.put_nowait(job)
        await release[job].wait()

    monkeypatch.setattr(store, 'claim', lambda: next(work, None))
    monkeypatch.setattr(queue, 'diagnose_job', handle)
    await queue.start()
    try:
        assert [await asyncio.wait_for(started.get(), 1) for _ in range(3)] == [1, 2, 3]
        store.model_connections.update_default_runtime({'concurrency': 1})
        await queue.apply_settings()
        release[1].set()
        release[2].set()
        await asyncio.sleep(0.35)
        assert started.empty()
        release[3].set()
        assert await asyncio.wait_for(started.get(), 1) == 4
    finally:
        await queue.stop()
