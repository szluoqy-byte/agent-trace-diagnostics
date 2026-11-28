import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient
from tracelens.config import Settings
from tracelens.demo import demo_trace, demo_report
from tracelens.execution import snapshot
from tracelens.main import create_app
from tracelens.queue import JobQueue
from tracelens.store import Store


def setup(tmp_path, count=20):
    settings = Settings(data_dir=tmp_path,model='snapshot-model',base_url='http://127.0.0.1:9/v1',
                        enable_validation_data=True)
    app = create_app(settings)
    ids = []
    for i in range(count):
        trace = demo_trace()
        trace.task_id = f'case-{i+1:03}'
        trace.title = f'案例 {i+1:03}'
        ids.append(app.state.store.add_trace(trace)['id'])
    return app, TestClient(app), ids


def create(client, ids, **extra):
    response = client.post('/api/tasks',json={'name':'任务 A','trace_ids':ids,**extra})
    assert response.status_code==200, response.text
    return response.json()


def endpoint(task):
    return f"/api/tasks/{task['task_id']}/runs/{task['run_id']}"


def complete(store, task):
    for row in store.run_rows(task['run_id']):
        store.update(row['job_id'],status='completed',stage='诊断完成',progress=100,result=demo_report())


def test_task_create_pagination_filter_export_and_restart(tmp_path):
    app,client,ids=setup(tmp_path)
    draft=create(client,ids,auto_feedback=True,diagnosis_mode='trajdebug')
    assert app.state.store.jobs()==[]
    assert client.get('/api/tasks').json()[0]['status']=='draft'
    assert client.get(f"/api/tasks/{draft['task_id']}").json()['draft_auto_feedback'] is True
    draft_path=f"/api/tasks/{draft['task_id']}/runs/draft"
    preview=client.get(draft_path+'/traces?page=2').json()
    assert preview['read_only'] and preview['total']==20 and preview['items'][0]['id']==ids[10]
    detail=client.get(draft_path+'/traces/'+ids[10]).json()
    assert detail['trace']['id']==ids[10] and detail['job'] is None
    assert detail['navigation']['previous_id']==ids[9]
    assert client.get(draft_path+'/traces/not-a-member').status_code==404
    assert client.post(draft_path+'/actions',json={'action':'pending'}).status_code==404
    run=client.post(f"/api/tasks/{draft['task_id']}/runs",json={}).json()['run_id']
    task={**draft,'run_id':run}
    first=client.get(endpoint(task)+'/traces').json()
    assert first['total']==20 and len(first['items'])==10 and first['pages']==2
    selection = client.get(endpoint(task)+'/traces?selection=true&search=case-01').json()
    assert selection == {'items': ids[9:19]}
    second=client.get(endpoint(task)+'/traces?page=2').json()
    assert second['items'][0]['task_id']=='case-011'
    assert client.get(endpoint(task)+'/traces?page=99').json()['page']==2
    assert client.get(endpoint(task)+'/traces?page_size=50').json()['pages']==1
    assert client.get(endpoint(task)+'/traces?page_size=15').status_code==422
    empty=client.get(endpoint(task)+'/traces?search=missing&page=2').json()
    assert empty['page']==1 and empty['total']==0 and empty['counts']['total']==20
    detail=client.get(endpoint(task)+'/traces/'+ids[9]).json()
    assert detail['navigation']['next_id']==ids[10] and detail['navigation']['index']==9
    exported=client.get(endpoint(task)+'/export',params=[('trace_id',ids[0]),('trace_id',ids[10])]).json()
    assert len(exported['trajectories'])==2
    assert 'api_key' not in exported['analysis']['config']
    restarted=create_app(app.state.settings)
    assert restarted.state.store.task(draft['task_id'])['name']=='任务 A'
    assert len(restarted.state.store.run_rows(run))==20


def test_task_prompt_language_is_persisted_and_frozen_per_run(tmp_path):
    app, client, ids = setup(tmp_path, 1)
    task = create(client, ids, prompt_language='zh')
    assert client.get(f"/api/tasks/{task['task_id']}").json()['prompt_language'] == 'zh'
    run_id = client.post(f"/api/tasks/{task['task_id']}/runs", json={}).json()['run_id']
    assert app.state.store.runs(task['task_id'])[0]['config']['prompt_language'] == 'zh'
    assert app.state.store.job_config(app.state.store.run_rows(run_id)[0]['job_id'])['prompt_language'] == 'zh'


def test_bulk_review_updates_across_pages_and_reset_preserves_history(tmp_path):
    app, client, ids = setup(tmp_path, 13)
    store = app.state.store
    task = create(client, ids[:12], start=True)
    scope = endpoint(task)
    jobs = store.run_rows(task['run_id'])
    for index, row in enumerate(jobs[:11]):
        report = demo_report()
        if index == 0:
            report['comparison'] = {'state': 'aligned', 'primary': 'trajdebug', 'engines': ['trajdebug', 'chief']}
            report['engine_runs'] = {'trajdebug': {'status': 'completed'}, 'chief': {'status': 'completed',
                                     'report': {'attribution': dict(report['attribution'])}}}
        store.update(row['job_id'], status='completed', result=report)
    first_job = jobs[0]['job_id']
    manual = {'decision': 'modified', 'critical_step': 7, 'summary': '已人工修改', 'category': '', 'notes': ''}
    assert client.post(f'/api/jobs/{first_job}/reviews', json=manual).status_code == 200
    action = {'decision': 'confirmed', 'trace_ids': [ids[0], ids[-1]]}
    assert client.post(scope+'/reviews', json=action).status_code == 422
    assert store.reviews(first_job)[0]['data'] == manual

    select = client.get(scope+'/traces', params={'selection': 'true'}).json()['items']
    action = {'decision': 'confirmed', 'trace_ids': select, 'notes': '同批复核'}
    result = client.post(scope+'/reviews', json=action).json()
    assert result['updated'] == 11 and result['skipped'] == [
        {'trace_id': ids[11], 'title': '案例 012', 'reason': '尚未完成诊断'},
    ]
    page = client.get(scope+'/traces', params={'review': 'confirmed', 'page': 2}).json()
    assert page['total'] == 11 and page['counts']['reviewed'] == 11 and page['items'][0]['id'] == ids[10]
    confirmed = store.reviews(first_job)[0]['data']
    assert confirmed['decision'] == 'confirmed' and confirmed['critical_step'] == 4 and confirmed['notes'] == '同批复核'
    assert client.post(scope+'/reviews', json=action).json()['updated'] == 0

    rejected = client.post(scope+'/reviews', json={'decision': 'rejected', 'trace_ids': select[:11]}).json()
    assert rejected['updated'] == 11 and client.get(scope+'/traces?review=rejected').json()['total'] == 11
    assert store.reviews(first_job)[0]['data']['critical_step'] is None
    reset = client.post(scope+'/reviews', json={'decision': 'reset', 'trace_ids': select[:11]}).json()
    assert reset['updated'] == 11
    assert client.get(scope+'/traces?review=waived').json()['total'] == 1
    assert client.get(scope+'/traces?review=pending').json()['total'] == 10
    assert store.reviews(first_job) == []
    exported = client.get(f'/api/jobs/{first_job}/export').json()
    assert exported['job']['review_status'] == 'waived' and exported['reviews'] == []
    assert [event['data']['decision'] for event in exported['review_history']] == ['reset', 'rejected', 'confirmed', 'modified']
    assert client.post(f'/api/jobs/{first_job}/reviews', json=manual).status_code == 200
    assert [event['data']['decision'] for event in store.reviews(first_job)] == ['modified']


def test_retries_stay_in_analysis_and_history_is_read_only(tmp_path):
    app,client,ids=setup(tmp_path,2)
    task=create(client,ids,start=True)
    original=app.state.store.run_rows(task['run_id'])[0]['job_id']
    app.state.store.update(original,status='failed',error='timeout',result={'partial':'keep'})
    response=client.post(endpoint(task)+'/actions',json={'action':'retry_failed'})
    assert response.json()['enqueued']==1
    attempts=app.state.store.item_attempts(task['run_id'],ids[0])
    assert len(attempts)==2 and attempts[0]['attempt']==2
    assert app.state.store.job(original)['result']=={'partial':'keep'}
    assert client.post(f'/api/jobs/{original}/retry').status_code==409
    complete(app.state.store,task)
    job=app.state.store.run_rows(task['run_id'])[0]['job_id']
    review={'decision':'modified','critical_step':7,'category':'人工分类','summary':'人工结论','notes':'note'}
    assert client.post(f'/api/jobs/{job}/reviews',json=review).status_code==200
    reviewed = client.get(endpoint(task) + '/traces').json()['items'][0]
    assert reviewed['review_status'] == 'modified' and reviewed['critical_step'] == 7
    original_result=app.state.store.job(job)['result']
    next_run=client.post(f"/api/tasks/{task['task_id']}/runs",json={}).json()['run_id']
    assert next_run!=task['run_id']
    assert client.get(endpoint(task)+'/traces').json()['read_only'] is True
    for action in ['pending','retry_failed','diagnose','feedback']:
        assert client.post(endpoint(task)+'/actions',json={'action':action}).status_code==409
    assert client.post(f'/api/jobs/{job}/reviews',json=review).status_code==409
    assert client.post(endpoint(task)+'/reviews',json={'decision':'reset','trace_ids':[ids[0]]}).status_code==409
    assert client.post(f'/api/jobs/{job}/feedback').status_code==409
    assert client.get(f'/api/jobs/{job}').json()['read_only'] is True
    assert app.state.store.job(job)['result']==original_result
    assert app.state.store.reviews(job)[0]['data']['summary']=='人工结论'
    assert all(r['review_status'] is None for r in app.state.store.run_rows(next_run))


def test_reanalysis_exposes_current_results_and_retires_replaced_managed_results(tmp_path):
    app, client, ids = setup(tmp_path, 1)
    store = app.state.store
    original = create(client, ids, start=True)
    complete(store, original)
    task_path = '/api/tasks/' + original['task_id']
    with store.connect() as db:
        db.execute("UPDATE analysis_runs SET config=json_remove(config,'$.latest_only') WHERE id=?", (original['run_id'],))
    prior = {**original, 'run_id': client.post(task_path + '/runs', json={}).json()['run_id']}
    complete(store, prior)
    prior_job = store.run_rows(prior['run_id'])[0]['job_id']
    current = {**original, 'run_id': client.post(task_path + '/runs', json={}).json()['run_id']}
    failed = store.run_rows(current['run_id'])[0]['job_id']
    store.update(failed, status='failed')

    assert [r['id'] for r in client.get(task_path).json()['runs']] == [current['run_id']]
    history = client.get(f'/api/trace-library/{ids[0]}/history?latest_only=true').json()
    assert [row['job_id'] for row in history['diagnoses']] == [failed]
    with TestClient(app) as client:
        assert store.job(prior_job)['status'] == 'completed'
        assert client.post(endpoint(current) + '/actions', json={'action': 'retry_failed'}).json()['enqueued'] == 1
        deadline = monotonic() + 8
        while store.job(prior_job) and monotonic() < deadline:
            sleep(0.05)
        assert store.job(prior_job) is None
        assert [r['id'] for r in store.runs(original['task_id'])] == [current['run_id'], original['run_id']]
        assert client.get(endpoint(original) + '/traces').json()['read_only'] is True
        assert client.get(endpoint(current) + '/traces').json()['status'] == 'completed'
        assert store.trace(ids[0]) is not None


def test_shared_trace_tasks_have_independent_jobs_and_atomic_actions(tmp_path):
    app,client,ids=setup(tmp_path,1)
    with ThreadPoolExecutor(2) as pool:
        tasks=list(pool.map(lambda _:create(client,ids,start=True),range(2)))
    jobs=[app.state.store.run_rows(t['run_id'])[0]['job_id'] for t in tasks]
    assert jobs[0]!=jobs[1]
    assert len(app.state.store.traces())==1
    assert len(Store(tmp_path).jobs())==2  # Restart must not recreate a global trace-level unique index.
    task=tasks[0]
    assert client.post(f"/api/tasks/{task['task_id']}/runs",json={}).status_code==409
    other=create(client,[app.state.store.add_trace(demo_trace())['id']],start=True)
    foreign=app.state.store.run_rows(other['run_id'])[0]['id']
    assert client.post(endpoint(task)+'/actions',json={'action':'diagnose','trace_ids':[ids[0],foreign]}).status_code==422
    assert client.get(endpoint(task)+'/export',params={'trace_id':foreign}).status_code==422
    assert client.get(f"/api/tasks/{task['task_id']}/runs/{other['run_id']}/traces").status_code==404


def test_trace_library_deduplicates_shared_traces_and_links_history(tmp_path):
    app, client, ids = setup(tmp_path, 11)
    original = create(client, ids[:1], start=True)
    complete(app.state.store, original)
    next_run = client.post(f"/api/tasks/{original['task_id']}/runs", json={}).json()['run_id']
    reuse = create(client, [ids[0], ids[-1]])

    first = client.get('/api/trace-library').json()
    second = client.get('/api/trace-library?page=2').json()
    assert first['total'] == 11 and first['pages'] == 2
    assert {row['id'] for row in first['items'] + second['items']} == set(ids)
    assert len(second['items']) == 1
    selection = client.get('/api/trace-library?selection=true&business=failed').json()
    assert {row['id'] for row in selection['items']} == set(ids)
    assert all(set(row) == {'id', 'title', 'outcome', 'input_format'} for row in selection['items'])
    diagnosed = client.get('/api/trace-library', params={'diagnosis': 'completed', 'search': 'CASE-001'}).json()
    assert diagnosed['total'] == 1
    assert diagnosed['items'][0]['id'] == ids[0] and diagnosed['items'][0]['task_count'] == 2
    filtered_selection = client.get('/api/trace-library?selection=true&diagnosis=completed&search=CASE-001').json()
    assert [row['id'] for row in filtered_selection['items']] == [ids[0]]
    assert client.get('/api/trace-library?diagnosis=completed&input_format=otel_genai').json()['total'] == 0
    assert client.get('/api/trace-library?page_size=17').status_code == 422

    history = client.get(f'/api/trace-library/{ids[0]}/history').json()
    assert {row['id'] for row in history['tasks']} == {original['task_id'], reuse['task_id']}
    assert [(row['run_id'], row['status']) for row in history['diagnoses']] == [
        (next_run, 'queued'), (original['run_id'], 'completed'),
    ]
    detail = client.get(endpoint(original) + '/traces/' + ids[0]).json()
    assert detail['job']['id'] == history['diagnoses'][1]['job_id'] and detail['read_only']
    assert client.get(f'/api/trace-library/{ids[-1]}/history').json()['diagnoses'] == []
    assert client.get('/api/trace-library/missing/history').status_code == 404


def test_preview_mixed_formats_exclusion_unknown_dedup_and_atomic_create(tmp_path, deepagent_template):
    app,client,_=setup(tmp_path,1)
    deepagent_template['outcome'] = 'succeeded'
    otel=(Path(__file__).parents[2]/'examples/otel-genai.json').read_bytes()
    files=[('files',('deepagent.json',json.dumps(deepagent_template),'application/json')),('files',('otel.json',otel,'application/json')),
           ('files',('old.json',json.dumps({'messages':[{'role':'assistant','content':'legacy'}]}),'application/json'))]
    response=client.post('/api/import/preview',files=files)
    assert response.status_code==200,response.text
    preview=response.json()
    assert {r['input_format'] for r in preview['rows']}=={'deepagent','otel_genai'}
    assert '仅支持 DeepAgent' in preview['files'][2]['errors'][0]
    before=len(app.state.store.traces())
    body={'name':'mixed','preview_id':preview['id'],'unknown_outcome':'failed'}
    assert client.post('/api/tasks',json=body).status_code==422
    assert len(app.state.store.traces())==before and app.state.store.tasks()==[]
    body['excluded_files']=['2']
    task=client.post('/api/tasks',json=body)
    assert task.status_code==200,task.text
    saved=app.state.store.task_inputs(task.json()['task_id'])
    assert {t['data']['outcome'] for t in saved}=={'failed','succeeded'}
    assert any(t['data']['otel'] for t in saved)
    duplicate=client.post('/api/import/preview',files=[('files',('a.json',json.dumps(deepagent_template),'application/json')),('files',('b.json',json.dumps(deepagent_template),'application/json'))]).json()
    assert duplicate['duplicates']==1 and len(duplicate['rows'])==2
    result=client.post('/api/tasks',json={'name':'dedup','preview_id':duplicate['id'],'excluded_files':['0']})
    assert result.status_code==200
    assert len(app.state.store.task_inputs(result.json()['task_id']))==1
    assert len(app.state.store.traces())==len(saved)+before


def test_unknown_outcome_and_missing_model_cannot_start(tmp_path, deepagent_template):
    app,client,_=setup(tmp_path,1)
    raw=deepagent_template
    p=client.post('/api/import/preview',files={'files':('u.json',json.dumps(raw),'application/json')}).json()
    body={'name':'unknown','preview_id':p['id']}
    assert client.post('/api/tasks',json=body).status_code==422
    app.state.settings.model=''
    assert client.post('/api/tasks',json={**body,'unknown_outcome':'failed','start':True}).status_code==409
    assert client.post('/api/tasks',json={**body,'unknown_outcome':'failed'}).status_code==200


def test_legacy_migration_is_idempotent_and_retains_reports_reviews_feedback(tmp_path):
    store=Store(tmp_path)
    t=store.add_trace(demo_trace())
    first=store.enqueue(t['id'],'demo');store.update(first['id'],status='failed',result={'partial':'legacy'})
    second=store.enqueue(t['id'],'demo');store.update(second['id'],status='completed',result=demo_report())
    store.add_review(second['id'],{'summary':'retain'})
    feedback=store.enqueue_feedback(second['id']);store.update_feedback(feedback['id'],status='completed',result={'hint':'retain'})
    before=store.jobs()
    with store.connect() as db:db.execute("DELETE FROM workspace_meta WHERE key='task_migration_v1'")
    migrated=Store(tmp_path)
    assert len(migrated.tasks())==1 and len(migrated.runs(migrated.tasks()[0]['id']))==2
    assert migrated.jobs()==before
    assert migrated.reviews(second['id'])[0]['data']['summary']=='retain'
    assert migrated.latest_feedback(second['id'])['result']=={'hint':'retain'}
    assert len(Store(tmp_path).tasks())==1
    connection_id = migrated.model_connections.add(name='Workspace default', model='saved-model',
        base_url='http://127.0.0.1:1/v1', api_key='saved-key', headers={}, make_default=True)
    app = create_app(Settings(data_dir=tmp_path, model='environment-model', base_url=''))
    config = app.state.store.job_config(first['id'])
    assert config['model_connection_id'] == connection_id and config['model'] == 'saved-model'
    assert 'api_key' not in config


async def test_queue_uses_frozen_config_and_auto_feedback(tmp_path,monkeypatch):
    app,client,ids=setup(tmp_path,2)
    for trace_id in ids:
        with app.state.store.connect() as db:
            data=json.loads(db.execute('SELECT data FROM traces WHERE id=?',(trace_id,)).fetchone()[0]);data['kind']='imported';db.execute('UPDATE traces SET data=? WHERE id=?',(json.dumps(data),trace_id))
    task=create(client,ids,start=True,auto_feedback=True,diagnosis_mode='trajdebug')
    seen=[]
    async def diagnose(trace,settings,progress):
        seen.append(('diagnosis',settings.model));return demo_report()
    async def feedback(job,settings,feedback_id):
        seen.append(('feedback',settings.model));return {'hint':'test'}
    monkeypatch.setattr('tracelens.queue.diagnose',diagnose)
    monkeypatch.setattr('tracelens.queue.generate_feedback',feedback)
    monkeypatch.setattr('tracelens.queue.feedback_availability',lambda store,job,settings:(True,''))
    app.state.settings.model='changed-after-start'
    queue=JobQueue(app.state.store,app.state.settings)
    await queue.start()
    try:
        for _ in range(100):
            if all(r['feedback_status']=='completed' for r in app.state.store.run_rows(task['run_id'])):break
            await asyncio.sleep(.02)
        assert all(r['feedback_status']=='completed' for r in app.state.store.run_rows(task['run_id']))
    finally:await queue.stop()
    assert seen.count(('diagnosis','snapshot-model'))==2 and seen.count(('feedback','snapshot-model'))==2
    assert 'api_key' not in json.dumps(snapshot(app.state.settings))
