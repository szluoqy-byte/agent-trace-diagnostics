import hashlib
import json
from fastapi.testclient import TestClient
import pytest
from tracelens.config import Settings
from tracelens.demo import demo_trace
from tracelens.main import create_app
from tracelens.normalize import fingerprint
from tracelens.official_samples import load_official_samples
from tracelens.trajdebug import UPSTREAM_COMMIT, to_unified


def write_corpus(directory):
    directory.mkdir(parents=True,exist_ok=True)
    cases=[]
    for i in range(2):
        data={'messages':[{'step':0,'role':'user','content':'Task '+str(i),'judgable':False},
                          {'step':1,'role':'assistant','content':'wrong action','judgable':True}],
              'metadata':{'dataset':'trajerrbench-test','task_id':str(i),'task_description':'Task '+str(i),
                          'reward':0,'annotation':{'critical_error_step':1},'extra':{}}}
        body=json.dumps(data).encode();name=str(i)+'.json';(directory/name).write_bytes(body)
        cases.append({'file':name,'title':'Official '+str(i),'task_id':str(i),'source_url':'https://example.test/'+name,
                      'sha256':hashlib.sha256(body).hexdigest(),
                      'git_blob_sha1':hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()})
    manifest={'commit':UPSTREAM_COMMIT,'cases':cases}
    (directory/'trajdebug-testset.json').write_text(json.dumps(manifest))
    return manifest


def test_official_corpus_is_verified_and_gold_stays_out_of_model_input(tmp_path):
    write_corpus(tmp_path)
    traces=load_official_samples(tmp_path)
    assert len(traces)==2 and len({fingerprint(t) for t in traces})==2
    assert traces[0].title=='Official 0'
    assert traces[0].annotation['critical_error_step']==1
    assert all(to_unified(t)['metadata']['annotation']=={} for t in traces)
    (tmp_path/'1.json').write_text('{}')
    with pytest.raises(ValueError,match='哈希'):
        load_official_samples(tmp_path)


def test_api_validates_entire_corpus_before_import_and_is_idempotent(tmp_path):
    sources=tmp_path/'sources';manifest=write_corpus(sources)
    original=(sources/'1.json').read_bytes();(sources/'1.json').write_text('{}')
    with TestClient(create_app(Settings(data_dir=tmp_path, enable_validation_data=True))) as client:
        before=len(client.get('/api/traces').json())
        assert client.post('/api/samples').status_code==409
        assert len(client.get('/api/traces').json())==before
        (sources/'1.json').write_bytes(original)
        first=client.post('/api/samples');assert first.status_code==200
        second=client.post('/api/samples');assert second.json()==first.json()
        assert len(client.get('/api/traces').json())==before+2
        assert client.get('/api/jobs').json()==[]


def test_formal_workspace_starts_empty_and_disables_validation_entry_points(tmp_path):
    write_corpus(tmp_path/'sources')
    settings = Settings(data_dir=tmp_path, enable_validation_data=False)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get('/api/traces').json() == []
        assert client.get('/api/trace-library').json()['total'] == 0
        assert client.get('/api/tasks?kind=all').json() == []
        assert client.post('/api/samples').status_code == 404
        assert client.post('/api/import/samples/preview').status_code == 404
        trace = app.state.store.add_trace(demo_trace())
        assert client.post('/api/jobs', json={'trace_ids':[trace['id']], 'mode':'demo'}).status_code == 422
        assert client.post('/api/tasks', json={'name':'验证', 'trace_ids':[trace['id']]}).status_code == 422
        assert client.get('/api/jobs').json() == []
    with TestClient(create_app(Settings(data_dir=tmp_path, enable_validation_data=True))) as client:
        assert client.post('/api/import/samples/preview').json()['files'][0]['count'] == 2


def test_restart_does_not_reinsert_demo_into_official_corpus(tmp_path):
    from tracelens.store import Store
    sources=tmp_path/'sources';write_corpus(sources)
    store=Store(tmp_path)
    for trace in load_official_samples(sources):store.add_trace(trace)
    for _ in range(2):
        with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
            traces=client.get('/api/traces').json()
            assert len(traces)==2 and all(t['kind']=='imported' for t in traces)


def test_official_manifest_rejects_path_escape(tmp_path):
    manifest=write_corpus(tmp_path/'sources')
    manifest['cases'][0]['file']='../../outside.json'
    (tmp_path/'sources/trajdebug-testset.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='路径'):
        load_official_samples(tmp_path/'sources')
