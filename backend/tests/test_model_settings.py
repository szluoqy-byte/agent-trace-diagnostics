import stat

import httpx
from fastapi.testclient import TestClient

from tracelens.config import Settings
from tracelens.execution import job_settings
from tracelens.main import create_app
from tracelens.normalize import normalize


PATH = '/api/model-configurations'


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
    assert client.get(PATH).json() == {'items': []}
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
