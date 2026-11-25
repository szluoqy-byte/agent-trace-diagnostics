from uuid import uuid4

import pytest

from test_patterns import begin, create as create_pattern, fixture
from test_tasks import create, setup

from tracelens.main import create_app


def artifact(tmp_path, kind, artifact_id):
    path = tmp_path / 'trajdebug' / kind / artifact_id
    path.mkdir(parents=True)
    (path / 'result.json').write_text('{}', encoding='utf-8')
    return path


def test_delete_diagnosis_removes_its_records_and_retains_shared_trace(tmp_path):
    app, client, (trace_id,) = setup(tmp_path, 1)
    store = app.state.store
    task = create(client, [trace_id], start=True)
    other = create(client, [trace_id])
    task_url = '/api/tasks/' + task['task_id']
    trace_url = '/api/traces/' + trace_id
    job_id = store.run_rows(task['run_id'])[0]['job_id']
    assert client.delete(task_url).status_code == 409

    run_id = uuid4().hex
    run_files = artifact(tmp_path, 'runs', run_id)
    store.update(job_id, status='completed', result={'provenance': {'run_id': run_id}})
    store.add_review(job_id, {'notes': 'Review'})
    feedback = store.enqueue_feedback(job_id)
    assert client.delete(task_url).status_code == 409
    store.update_feedback(feedback['id'], status='completed')
    feedback_files = artifact(tmp_path, 'feedback', feedback['id'])

    assert client.delete(task_url).json() == {'deleted': True}
    assert client.delete(task_url).status_code == 404
    assert client.get('/api/jobs/' + job_id).status_code == 404
    assert not run_files.exists() and not feedback_files.exists()
    assert store.reviews(job_id) == [] and store.feedback_history(job_id) == []
    assert client.get(trace_url).status_code == 200
    history = client.get(f'/api/trace-library/{trace_id}/history').json()
    assert [task['id'] for task in history['tasks']] == [other['task_id']]
    assert history['diagnoses'] == []
    assert client.delete(trace_url).status_code == 409
    assert client.delete('/api/tasks/' + other['task_id']).status_code == 200
    assert client.delete(trace_url).status_code == 200
    assert client.get(trace_url).status_code == 404


@pytest.mark.parametrize('from_library', [False, True])
def test_delete_pattern_preserves_source_diagnoses_and_releases_references(tmp_path, from_library):
    app, client, source, ids = fixture(tmp_path)
    scope = {'trace_ids': ids[:3]} if from_library else {'task_id': source['task_id'], 'run_id': source['run_id']}
    preview = begin(client, scope)
    _, response = create_pattern(client, preview)
    assert response.status_code == 200, response.text
    pattern = response.json()
    run_path = f"/api/pattern-tasks/{pattern['task_id']}/runs/{pattern['run_id']}"
    pattern_id = client.get(run_path).json()['result']['patterns'][0]['id']
    assert client.post(f'{run_path}/patterns/{pattern_id}/review', json={'decision': 'suspect'}).status_code == 200

    source_url = '/api/tasks/' + source['task_id']
    assert client.delete(source_url).status_code == 409
    assert client.delete('/api/traces/' + ids[0]).status_code == 409
    assert client.delete('/api/tasks/' + pattern['task_id']).status_code == 200
    assert client.get(run_path).status_code == 404
    assert client.get(source_url).status_code == 200
    assert len(app.state.store.jobs()) == 3
    assert client.delete(source_url).status_code == 200


def test_delete_unlinked_trace_cleans_standalone_jobs_and_retries_artifacts(tmp_path, monkeypatch):
    app, client, (trace_id,) = setup(tmp_path, 1)
    store = app.state.store
    path = '/api/traces/' + trace_id
    job = store.enqueue(trace_id, 'demo', config={'model': 'test'})
    assert client.delete(path).status_code == 409
    assert store.claim()['id'] == job['id']
    assert client.delete(path).status_code == 409
    run_id = uuid4().hex
    files = artifact(tmp_path, 'runs', run_id)
    store.update(job['id'], status='failed', result={'provenance': {'run_id': run_id}})

    def locked(_path):
        raise PermissionError

    with monkeypatch.context() as context:
        context.setattr('tracelens.deletion.shutil.rmtree', locked)
        assert client.delete(path).status_code == 200
        assert files.exists()
    assert client.delete(path).status_code == 404
    assert store.jobs() == [] and store.job_config(job['id']) is None
    assert create_app(app.state.settings).state.store.trace(trace_id) is None
    assert not files.exists()
