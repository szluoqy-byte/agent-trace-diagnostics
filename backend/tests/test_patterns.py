import json
from uuid import uuid4

from fastapi.testclient import TestClient

from tracelens.config import Settings
from tracelens.demo import demo_report, demo_trace
from tracelens.main import create_app
from tracelens.pattern_analysis import object_key


def fixture(tmp_path):
    settings = Settings(data_dir=tmp_path, model='fixture', base_url='http://127.0.0.1:9/v1')
    app = create_app(settings)
    store = app.state.store
    ids = []
    for index in range(5):
        trace = demo_trace()
        trace.kind = 'imported'
        trace.task_id = f'pattern-fixture-{index}'
        trace.title = f'样本 {index + 1}'
        if index == 4:
            trace.outcome = 'succeeded'
        ids.append(store.add_trace(trace)['id'])
    client = TestClient(app)
    task = client.post('/api/tasks', json={'name': '测试诊断', 'trace_ids': ids[:3], 'start': True}).json()
    for index, member in enumerate(store.run_rows(task['run_id'])):
        report = demo_report()
        report.update(mode='live', model='fixture')
        report['instances'][0]['object'] = ['用户明确确认', 'explicit user confirmation', '退款额度限制'][index]
        store.update(member['job_id'], status='completed', result=report)
    return app, client, task, ids


def begin(client, scope):
    return client.post('/api/pattern-tasks/preview', json={'scope': scope}).json()


def create(client, preview):
    body = {'name': '重复失败', 'scope': preview['scope'], 'snapshot_hash': preview['snapshot_hash'],
            'request_id': str(uuid4())}
    return body, client.post('/api/pattern-tasks', json=body)


def test_pattern_counts_snapshot_exports_and_task_views(tmp_path):
    app, client, task, ids = fixture(tmp_path)
    preview = begin(client, {'trace_ids': [ids[0], *ids]})
    assert preview['counts'] == {'included': 3, 'pending': 1, 'succeeded': 1}
    body, response = create(client, preview)
    assert response.status_code == 200, response.text
    created = response.json()
    assert client.post('/api/pattern-tasks', json=body).json() == created
    assert client.post('/api/pattern-tasks', json={**body,'name':'另一任务'}).status_code == 409
    assert len(client.get('/api/tasks').json()) == 1
    assert {row['kind'] for row in client.get('/api/tasks?kind=all').json()} == {'diagnosis','failure_patterns'}
    path = f"/api/pattern-tasks/{created['task_id']}/runs/{created['run_id']}"
    report = client.get(path).json()['result']
    assert (report['selected'],report['eligible'],report['business_failed'],report['repeated']) == (5,3,4,1)
    repeated, single = report['patterns']
    assert (repeated['count'],repeated['denominator'],single['count']) == (2,3,1)
    members = client.get(path+'/members', params={'pattern_id':repeated['id']}).json()['items']
    assert {row['trace_id'] for row in members} == set(ids[:2])
    assert all(row['source_run_id'] == task['run_id'] and row['evidence']['source_kind'] == 'original' for row in members)
    assert client.get(path+'/members?status=excluded').json()['total'] == 2
    assert len(client.get(path+'/export?format=samples').json()['samples']) == 2
    assert '2/3' in client.get(path+'/export?format=md').text
    assert app.state.store.jobs() and len(app.state.store.jobs()) == 3
    review_path = path+'/patterns/'+repeated['id']+'/review'
    assert client.post(review_path,json={'decision':'suspect','notes':'请检查'}).status_code == 200
    assert client.get(path).json()['reviews'][repeated['id']]['decision'] == 'suspect'
    rerun_path = f"/api/pattern-tasks/{created['task_id']}/runs"
    rerun = client.post(rerun_path,json={key:value for key,value in {**body,'request_id':str(uuid4())}.items()
                                       if key!='name'})
    assert rerun.status_code == 200, rerun.text
    assert client.post(review_path,json={'decision':'confirmed'}).status_code == 409


def test_pattern_pins_review_and_validates_scope(tmp_path):
    app, client, task, ids = fixture(tmp_path)
    scope = {'task_id': task['task_id'], 'run_id': task['run_id']}
    preview = begin(client, scope)
    assert preview['counts']['included'] == 3
    body, response = create(client, preview)
    assert response.status_code == 200, response.text
    created = response.json()
    source = app.state.store.run_rows(task['run_id'])[0]
    app.state.store.add_review(source['job_id'], {'decision':'modified','critical_step':2,'category':'人工分类',
                                                 'summary':'人工修订','notes':''})
    updated = begin(client, scope)
    assert updated['counts'] == {'included': 2, 'review_needs_evidence': 1}
    body['request_id'] = str(uuid4())
    assert client.post('/api/pattern-tasks', json=body).status_code == 409
    const_path = f"/api/pattern-tasks/{created['task_id']}/runs/{created['run_id']}"
    previous = client.get(const_path+'/members', params={'trace_id':ids[0]}).json()['items'][0]
    assert previous['state'] == 'included' and previous['critical_step'] == 4 and previous['review_id'] is None
    assert client.post('/api/pattern-tasks/preview',json={'scope':{**scope,'trace_ids':[ids[-1]]}}).status_code == 422
    assert client.post('/api/pattern-tasks/preview',json={'scope':scope,'overrides':{ids[0]:'wrong'}}).status_code == 422
    _, too_few = create(client, begin(client, {'trace_ids':[ids[0],ids[3]]}))
    assert too_few.status_code == 422


def test_different_cancellation_failures_remain_singletons(tmp_path):
    app, client, task, ids = fixture(tmp_path)
    objects = ('Cancelled reservation AB1234 without obtaining explicit confirmation',
               'Cancellation eligibility for reservation BC2345 depends on the 24-hour window',
               'The cancelled reservation CD3456 was not for an upcoming flight')
    for row, obj in zip(app.state.store.run_rows(task['run_id']), objects):
        job = app.state.store.job(row['job_id'])
        job['result']['instances'][0]['object'] = obj
        app.state.store.update(job['id'], result=job['result'])
    _, response = create(client, begin(client, {'trace_ids': ids[:3]}))
    assert response.status_code == 200, response.text
    ids_created = response.json()
    path = f"/api/pattern-tasks/{ids_created['task_id']}/runs/{ids_created['run_id']}"
    result = client.get(path).json()['result']
    assert result['repeated'] == 0 and len(result['patterns']) == 3
    assert 'topics' not in result
    assert all('topic' not in row for row in client.get(path+'/members').json()['items'])
    assert client.get(path+'/export?format=samples').json()['samples'] == []
    with app.state.store.connect() as db:
        saved = db.execute('SELECT result FROM pattern_runs WHERE id=?', (ids_created['run_id'],)).fetchone()
        snapshots = db.execute('SELECT snapshot FROM pattern_run_inputs WHERE run_id=?', (ids_created['run_id'],))
        assert 'topics' not in json.loads(saved['result'])
        assert all('topic' not in json.loads(row['snapshot']) for row in snapshots)


def test_historical_topics_are_archived_but_not_served(tmp_path):
    app, client, _, ids = fixture(tmp_path)
    store = app.state.store
    _, response = create(client, begin(client, {'trace_ids': ids[:3]}))
    assert response.status_code == 200, response.text
    created = response.json()
    task_id, run_id = created['task_id'], created['run_id']
    run = store.pattern_run(task_id, run_id)
    repeated = run['result']['patterns'][0]
    path = f'/api/pattern-tasks/{task_id}/runs/{run_id}'
    legacy_id = 'legacy-topic'
    legacy_topic = {'id': legacy_id, 'object': '历史共性问题示例', 'count': 2,
                    'representatives': [ids[0], ids[2]], 'trace_ids': [ids[0], ids[2]]}
    legacy_member = {'key': legacy_id, 'check': 'user_confirmation'}
    with store.connect() as db:
        db.execute("UPDATE pattern_runs SET result=json_set(result,'$.topics',json(?)) WHERE id=?",
                   (json.dumps([legacy_topic]), run_id))
        db.execute("UPDATE pattern_run_inputs SET snapshot=json_set(snapshot,'$.topic',json(?)) "
                   "WHERE run_id=? AND trace_id=?", (json.dumps(legacy_member), run_id, ids[0]))
    store.add_pattern_review(run, legacy_id, 'confirmed', '历史主题备注')

    assert client.post(f"{path}/patterns/{repeated['id']}/review", json={'decision': 'confirmed'}).status_code == 200
    result = client.get(path).json()
    assert result['result'] == run['result']
    assert set(result['reviews']) == {repeated['id']}
    counts = next(row['counts'] for row in client.get('/api/tasks?kind=all').json() if row['id'] == task_id)
    assert 'topics' not in counts
    assert client.get(path+'/members', params={'pattern_id': legacy_id}).status_code == 404
    assert client.post(f'{path}/patterns/{legacy_id}/review', json={'decision': 'confirmed'}).status_code == 404
    members = client.get(path+'/members', params={'pattern_id': repeated['id']}).json()['items']
    assert {row['trace_id'] for row in members} == set(ids[:2])
    assert all('topic' not in row and row['evidence'] for row in members)
    samples = client.get(path+'/export?format=samples').json()['samples']
    assert {row['trace_id'] for row in samples} == set(ids[:2])
    assert all(row['group_ids'] == [repeated['id']] for row in samples)
    exported = client.get(path+'/export?format=json').json()
    assert exported['run']['result'] == run['result']
    assert all('topic' not in row for row in exported['inputs'])
    assert [review['pattern_id'] for review in exported['reviews']] == [repeated['id']]
    markdown = client.get(path+'/export?format=md').text
    assert '共性问题' not in markdown and '历史主题备注' not in markdown
    with store.connect() as db:
        saved = db.execute('SELECT result FROM pattern_runs WHERE id=?', (run_id,)).fetchone()
        snapshot = db.execute('SELECT snapshot FROM pattern_run_inputs WHERE run_id=? AND trace_id=?',
                              (run_id, ids[0])).fetchone()
        assert json.loads(saved['result'])['topics'] == [legacy_topic]
        assert json.loads(snapshot['snapshot'])['topic'] == legacy_member
        assert db.execute('SELECT 1 FROM pattern_reviews WHERE run_id=? AND pattern_id=?',
                          (run_id, legacy_id)).fetchone()


def test_object_normalization_keeps_thresholds():
    assert object_key('Cancel reservation AB1234 after approval at step 13') == object_key(
        'Cancel reservation ZY4567 after approval at step 25')
    assert object_key('Cancellation within 24 hours') != object_key('Cancellation within 48 hours')
    assert object_key('Approval before step 2') != object_key('Approval before step 3')


def test_link_missing_instance_only_when_step_and_taxonomy_have_unique_support(tmp_path):
    app, client, task, _ = fixture(tmp_path)
    first, second, third = app.state.store.run_rows(task['run_id'])
    for row in (first, second, third):
        job = app.state.store.job(row['job_id'])
        report = job['result']
        report['attribution']['critical_instance_id'] = None
        if row == second:
            report['attribution']['category'] = 'plan.BadDecomposition'
        if row == third:
            report['instances'].append({**report['instances'][0], 'id': 'I-other'})
        app.state.store.update(job['id'], result=report)
    preview = begin(client, {'task_id': task['task_id'], 'run_id': task['run_id']})
    projected = {item['trace_id']: item for item in preview['items']}
    assert projected[first['id']]['instance_source'] == 'unique_same_step'
    assert projected[first['id']]['evidence']['step'] == 4
    for row in (second, third):
        assert projected[row['id']]['state'] == 'missing_instance'
        assert projected[row['id']]['critical_step'] == 4
        assert 'evidence' not in projected[row['id']]


def test_aggregate_prefers_critical_commitment_to_earlier_rule_quote(tmp_path):
    app, client, task, _ = fixture(tmp_path)
    first = app.state.store.run_rows(task['run_id'])[0]
    job = app.state.store.job(first['job_id'])
    report = job['result']
    anchor = report['triggers'][0]['commitment']
    anchor.update(source_kind='compressed', original_matched=False,
                  view_matches=[{'step':4, 'tier':'th1', 'start':0, 'end':len(anchor['quote'])}])
    report['compression_views'] = {'4': {'th1': anchor['quote']}}
    app.state.store.update(job['id'], result=report)
    preview = begin(client, {'trace_ids': [first['id']]})
    item, = preview['items']
    assert item['evidence']['step'] == item['critical_step'] == 4
    assert item['evidence']['source_kind'] == 'compressed'
    assert item['evidence_role'] == 'commitment'


def test_migration_repairs_saved_category_for_new_runs_and_preserves_snapshots(tmp_path):
    app, client, task, ids = fixture(tmp_path)
    store = app.state.store
    first, second, _ = store.run_rows(task['run_id'])
    first_job = store.job(first['job_id'])
    report = first_job['result']
    raw = report['official_output']
    raw['critical_error'].update(llm_pick_step=4, unified_critical_taxonomy_tag='reason.MissingAssumptionCheck')
    store.update(first_job['id'], result=report)
    store.add_review(second['job_id'], {'decision':'confirmed','critical_step':4,'category':'act.UnsafeOrForbiddenAction',
                                      'summary':'复核记录','notes':''})
    _, response = create(client, begin(client, {'trace_ids': ids[:3]}))
    assert response.status_code == 200, response.text
    saved = response.json()
    historical = store.pattern_run(saved['task_id'], saved['run_id'])
    assert historical['result']['repeated'] == 1
    with store.connect() as db:
        db.execute("DELETE FROM workspace_meta WHERE key='canonical_attribution_v1'")

    repaired_app = create_app(app.state.settings)
    repaired_store = repaired_app.state.store
    repaired_client = TestClient(repaired_app)
    repaired = repaired_store.job(first_job['id'])['result']
    assert repaired['official_output'] == raw
    assert repaired['attribution']['category'] == 'reason.MissingAssumptionCheck'
    assert repaired_store.run_rows(task['run_id'])[0]['category'] == 'reason.MissingAssumptionCheck'
    assert repaired_store.reviews(second['job_id']) == store.reviews(second['job_id'])
    assert repaired_store.pattern_run(saved['task_id'], saved['run_id']) == historical
    preview = begin(repaired_client, {'trace_ids': ids[:3]})
    rerun = repaired_client.post(f"/api/pattern-tasks/{saved['task_id']}/runs", json={
        'scope': preview['scope'], 'snapshot_hash':preview['snapshot_hash'], 'request_id':str(uuid4()),
    })
    assert rerun.status_code == 200, rerun.text
    assert repaired_store.pattern_run(saved['task_id'],rerun.json()['run_id'])['result']['repeated'] == 0
