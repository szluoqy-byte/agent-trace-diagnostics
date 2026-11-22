import json
from collections import Counter
from typing import Literal
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import Field
from .models import Contract, Trajectory
from .normalize import parse_upload, fingerprint
from .execution import active_execution, snapshot, job_settings, run_settings
from .feedback import feedback_availability
from .official_samples import load_official_samples


class TaskCreate(Contract):
    name: str = Field(min_length=1, max_length=100)
    agent: str = Field(default='', max_length=100)
    version: str = Field(default='', max_length=80)
    goal: str = Field(default='', max_length=3000)
    preview_id: str | None = None
    source_task_id: str | None = None
    trace_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)
    excluded_files: list[str] = Field(default_factory=list, max_length=50)
    unknown_outcome: Literal['', 'failed', 'succeeded'] = ''
    start: bool = False
    auto_feedback: bool = False


class RunCreate(Contract):
    auto_feedback: bool = False


class RunAction(Contract):
    action: Literal['pending', 'retry_failed', 'diagnose', 'feedback']
    trace_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)


def summary(rows):
    counts = Counter(row['diagnosis'] for row in rows)
    applicable = [r for r in rows if r.get('feedback_applicable')]
    counts.update(total=len(rows), business_failed=sum(r['outcome']=='failed' for r in rows),
                  business_succeeded=sum(r['outcome']=='succeeded' for r in rows),
                  feedback_applicable=len(applicable), feedback_completed=sum(r.get('feedback_status')=='completed' for r in applicable),
                  feedback_failed=sum(r.get('feedback_status')=='failed' for r in applicable),
                  feedback_active=sum(r.get('feedback_status') in {'queued','running'} for r in rows),
                  reviewed=sum(bool(r.get('review_status')) for r in rows),
                  review_pending=sum(r['diagnosis']=='completed' and not r.get('review_status') for r in rows))
    if counts['running'] or counts['queued']:
        status = 'running'
    elif counts['feedback_active']:
        status = 'feedback_running'
    elif counts['completed']==len(rows) and rows:
        status = 'completed'
    elif counts['completed']:
        status = 'partial'
    elif counts['failed']:
        status = 'failed'
    else:
        status = 'pending'
    categories = Counter(r.get('category') for r in rows if r['diagnosis']=='completed' and r.get('critical_step') is not None and r.get('category'))
    return {'counts': dict(counts), 'status': status, 'categories': [{'name': k, 'count': v} for k,v in categories.most_common()]}


def filter_rows(rows, search='', diagnosis='all', business='all', review='all', category='all'):
    if diagnosis not in {'all','pending','queued','running','completed','failed'} or business not in {'all','failed','succeeded','unknown'} or review not in {'all','pending','confirmed','modified','rejected'}:
        raise HTTPException(422, '无效筛选条件')
    return [r for r in rows if (not search or search.lower() in f"{r['title']} {r['id']} {r['task_id']} {r['source']}".lower())
            and (diagnosis=='all' or r['diagnosis']==diagnosis)
            and (business=='all' or r['outcome']==business)
            and (review=='all' or (r['diagnosis']=='completed' and not r.get('review_status') if review=='pending' else r.get('review_status')==review))
            and (category=='all' or r.get('category')==category)]


def attach_task_routes(app, store, settings, job_view):
    router = APIRouter(prefix='/api')

    def require_task(task_id):
        task = store.task(task_id)
        if not task or task['kind'] != 'diagnosis':
            raise HTTPException(404, '诊断任务不存在')
        return task

    def require_run(task_id, run_id, *, writable=False):
        require_task(task_id)
        runs = store.runs(task_id)
        run = next((r for r in runs if r['id']==run_id), None)
        if not run:
            raise HTTPException(404, '该分析记录不属于当前任务')
        if writable and run_id!=runs[0]['id']:
            raise HTTPException(409, '历史分析记录只读，请切换到最新分析')
        return run

    def checked(call):
        try:
            return call()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def inputs(task_id):
        return [dict(id=t['id'], **{k: t['data'][k] for k in ('title','task_id','source','kind','outcome','dataset')},
                     input_format='otel_genai' if t['data'].get('otel') else 'messages',
                     steps=len(t['data']['messages']), diagnosis='pending', position=index) for index,t in enumerate(store.task_inputs(task_id))]

    def task_view(task):
        runs = store.runs(task['id'])
        details = [{**run, **summary(store.run_rows(run['id'])), 'read_only': i>0} for i,run in enumerate(runs)]
        rows = store.run_rows(runs[0]['id']) if runs else inputs(task['id'])
        totals = summary(rows)
        return {**task, 'draft_auto_feedback': bool(task['draft_auto_feedback']),
                'runs': details, 'latest_run_id': runs[0]['id'] if runs else None,
                'input_count': len(rows), 'formats': sorted({r['input_format'] for r in rows}),
                **totals, 'status': totals['status'] if runs else 'draft'}

    def stage_preview(files):
        token = store.save_preview(files)
        seen, rows, duplicates = set(), [], 0
        for file in files:
            for value in file['traces']:
                trajectory = Trajectory.model_validate(value)
                digest = fingerprint(trajectory)
                if digest in seen:
                    duplicates += 1
                seen.add(digest)
                rows.append({'id': digest, 'file_id': file['id'], 'title': trajectory.title,
                             'task_id': trajectory.task_id, 'outcome': trajectory.outcome,
                             'fingerprints': {outcome: fingerprint(trajectory.model_copy(update={'outcome':outcome})) for outcome in ('failed','succeeded')} if trajectory.outcome=='unknown' else {},
                             'steps': len(trajectory.messages), 'input_format': 'otel_genai' if trajectory.otel else 'messages'})
        return {'id': token, 'files': [{k:v for k,v in f.items() if k!='traces'} for f in files], 'rows': rows, 'duplicates': duplicates}

    @router.post('/import/preview')
    async def preview_import(files: list[UploadFile] = File(...)):
        if len(files)>50:
            raise HTTPException(413, '每次最多上传 50 个文件')
        total, count, results = 0, 0, []
        for index,file in enumerate(files):
            content = await file.read(10*1024*1024+1)
            total += len(content)
            if len(content)>10*1024*1024 or total>20*1024*1024:
                raise HTTPException(413, '单文件最多 10MB，每次最多 20MB')
            parsed, errors = [], []
            try:
                parsed = parse_upload(content, file.filename or 'trajectory.json')
                if not parsed:
                    errors.append('文件中没有可用轨迹')
            except (ValueError, UnicodeDecodeError) as exc:
                errors.append(str(exc))
            count += len(parsed)
            if count>500:
                raise HTTPException(413, '每个任务最多 500 条轨迹')
            results.append({'id': str(index), 'name': file.filename or 'trajectory.json', 'errors': errors,
                            'count': len(parsed), 'formats': sorted({'otel_genai' if t.otel else 'messages' for t in parsed}),
                            'traces': [t.model_dump() for t in parsed]})
        return stage_preview(results)

    @router.post('/import/samples/preview')
    def preview_samples():
        if not settings.enable_validation_data:
            raise HTTPException(404, '当前工作区未启用评测数据导入')
        try:
            traces = load_official_samples(settings.data_dir/'sources')
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(409, '请先运行 scripts/fetch_samples.py 下载官方测试集') from exc
        return stage_preview([{'id':'official','name':'公开评测数据','errors':[], 'count':len(traces), 'formats':['messages'], 'traces':[t.model_dump() for t in traces]}])

    @router.get('/tasks')
    def tasks(kind: Literal['diagnosis', 'failure_patterns', 'all']='diagnosis'):
        result = []
        for task in store.tasks(kind):
            if task['kind'] == 'diagnosis':
                result.append(task_view(task))
            else:
                runs = store.pattern_runs(task['id'])
                latest = runs[0]
                result.append({**task, 'input_count': latest['selected'], 'status': 'completed',
                               'counts': {'eligible': latest['eligible'], 'repeated': latest['repeated']},
                               'runs': runs})
        return result

    @router.get('/tasks/{task_id}')
    def task(task_id: str):
        return task_view(require_task(task_id))

    def validate_inputs(traces, *, start, execution):
        if not traces or len(traces)>500:
            raise HTTPException(422, '每个任务需要 1–500 条轨迹')
        if not settings.enable_validation_data and any(t.kind == 'demo' for t in traces):
            raise HTTPException(422, '历史合成轨迹仅供查看，请选择待诊断的轨迹')
        if any(t.outcome=='unknown' for t in traces):
            raise HTTPException(422, '请补齐未知业务结果后保存任务，不会自动将未知项视为失败')
        if start and any(t.kind!='demo' for t in traces) and not execution.ready:
            raise HTTPException(409, '请在系统设置中启用默认诊断模型，或先保存草稿')

    @router.post('/tasks')
    def create_task(body: TaskCreate):
        if not body.name.strip() or sum(bool(v) for v in (body.preview_id,body.source_task_id,body.trace_ids))!=1:
            raise HTTPException(422, '请填写任务名称，并选择一种数据来源')
        trace_ids, traces = None, []
        if body.preview_id:
            preview = store.preview(body.preview_id)
            if preview is None:
                raise HTTPException(409, '上传校验已过期，请重新选择文件')
            if not set(body.excluded_files)<={f['id'] for f in preview}:
                raise HTTPException(422, '排除的文件不存在')
            seen = set()
            for file in preview:
                if file['id'] in body.excluded_files:
                    continue
                if file['errors']:
                    raise HTTPException(422, '请修正或排除有格式错误的文件')
                for value in file['traces']:
                    t = Trajectory.model_validate(value)
                    if t.outcome=='unknown' and body.unknown_outcome:
                        t.outcome = body.unknown_outcome
                    digest = fingerprint(t)
                    if digest not in seen:
                        seen.add(digest)
                        traces.append(t)
        else:
            if body.source_task_id:
                require_task(body.source_task_id)
                trace_ids = [t['id'] for t in store.task_inputs(body.source_task_id)]
            else:
                trace_ids = list(dict.fromkeys(body.trace_ids))
            for trace_id in trace_ids:
                trace = store.trace(trace_id)
                if not trace:
                    raise HTTPException(404, '复用的轨迹不存在')
                traces.append(Trajectory.model_validate(trace['data']))
        execution, connection_id = active_execution(store, settings)
        validate_inputs(traces, start=body.start, execution=execution)
        task_id, run_id = store.create_task({**body.model_dump(), 'name': body.name.strip()},
                                         traces=traces, trace_ids=trace_ids,
                                         config=snapshot(execution, connection_id) if body.start else None,
                                         auto_feedback=body.auto_feedback)
        return {'task_id':task_id, 'run_id':run_id}

    @router.post('/tasks/{task_id}/runs')
    def start_run(task_id: str, body: RunCreate):
        require_task(task_id)
        execution, connection_id = active_execution(store, settings)
        validate_inputs([Trajectory.model_validate(t['data']) for t in store.task_inputs(task_id)],
                        start=True, execution=execution)
        run_id = checked(lambda:store.start_run(task_id, snapshot(execution, connection_id), body.auto_feedback))
        return {'run_id':run_id}

    def trace_scope(task_id, run_id):
        if run_id == 'draft':
            require_task(task_id)
            return None, inputs(task_id), True
        run = require_run(task_id, run_id)
        return run, store.run_rows(run_id), store.runs(task_id)[0]['id'] != run_id

    @router.get('/tasks/{task_id}/runs/{run_id}/traces')
    def list_traces(task_id: str, run_id: str, page: int=Query(1,ge=1), page_size: int=Query(10,ge=1,le=50),
                    search: str='', diagnosis: str='all', business: str='all', review: str='all', category: str='all'):
        if page_size not in {10,20,50}:
            raise HTTPException(422, '每页条数仅支持 10、20、50')
        run, all_rows, read_only = trace_scope(task_id, run_id)
        rows = filter_rows(all_rows, search, diagnosis, business, review, category)
        pages = max(1,(len(rows)+page_size-1)//page_size)
        page = min(page,pages)
        return {'items': rows[(page-1)*page_size:page*page_size], 'total':len(rows),'page':page,'page_size':page_size,'pages':pages,
                'run':run, 'read_only':read_only, **summary(all_rows)}

    @router.get('/tasks/{task_id}/runs/{run_id}/traces/{trace_id}')
    def trace_detail(task_id: str, run_id: str, trace_id: str, search: str='', diagnosis: str='all', business: str='all', review: str='all', category: str='all'):
        run, rows, read_only = trace_scope(task_id, run_id)
        row = next((r for r in rows if r['id']==trace_id),None)
        if row is None:
            raise HTTPException(404, '轨迹不属于当前分析记录')
        filtered = filter_rows(rows,search,diagnosis,business,review,category)
        ids = [r['id'] for r in filtered]
        index = ids.index(trace_id) if trace_id in ids else -1
        previous = next((r['id'] for r in reversed(filtered) if r['position']<row['position']),None)
        following = next((r['id'] for r in filtered if r['position']>row['position']),None)
        return {'trace':store.trace(trace_id), 'job':job_view(row['job_id']) if row.get('job_id') else None,
                'attempts':store.item_attempts(run_id,trace_id) if run else [], 'read_only':read_only,
                'navigation':{'index':index,'total':len(ids),'previous_id':previous,'next_id':following}}

    @router.post('/tasks/{task_id}/runs/{run_id}/actions')
    def actions(task_id: str, run_id: str, body: RunAction):
        run = require_run(task_id,run_id,writable=True)
        rows = store.run_rows(run_id)
        selected = body.trace_ids if body.trace_ids is not None else [r['id'] for r in rows]
        if not set(selected)<={r['id'] for r in rows}:
            raise HTTPException(422, '所选轨迹不属于当前分析记录')
        if body.action!='feedback':
            statuses = {'pending': {'pending'}, 'retry_failed': {'failed'}, 'diagnose': {'pending', 'failed'}}
            chosen = [r for r in rows if r['id'] in selected and r['diagnosis'] in statuses[body.action]]
            if not settings.enable_validation_data and any(r['kind'] == 'demo' for r in chosen):
                raise HTTPException(422, '历史合成轨迹仅供查看，请选择待诊断的轨迹')
            if any(r['outcome'] == 'unknown' for r in chosen):
                raise HTTPException(422, '所选范围含未声明业务结果的轨迹，请先补齐结果')
            if any(r['kind']!='demo' for r in chosen):
                execution = run_settings(store, settings, run['config'])
                if not execution.ready:
                    raise HTTPException(409, '本次分析使用的模型配置不可用，请使用当前默认模型重新分析')
            jobs = checked(lambda:store.enqueue_items(run_id,selected,body.action))
            return {'enqueued':len(jobs),'job_ids':jobs,'skipped':[]}
        created, skipped = [], []
        for row in rows:
            if row['id'] not in selected:
                continue
            job = store.job(row['job_id']) if row['job_id'] else None
            latest = store.latest_feedback(job['id']) if job else None
            if latest and latest['status'] in {'queued','running','completed'}:
                continue
            eligible,reason = feedback_availability(job,job_settings(store,settings,job['id'])) if job else (False,'尚未完成诊断')
            if eligible:
                created.append(checked(lambda:store.enqueue_feedback(job['id'])))
            else:
                skipped.append({'trace_id':row['id'],'reason':reason})
        return {'enqueued':len(created),'skipped':skipped}

    @router.get('/tasks/{task_id}/runs/{run_id}/export')
    def export(task_id: str, run_id: str, trace_id: list[str] | None=Query(None)):
        run = require_run(task_id,run_id)
        rows = store.run_rows(run_id)
        if trace_id is not None and (not trace_id or not set(trace_id)<={r['id'] for r in rows}):
            raise HTTPException(422, '导出范围包含其他任务的轨迹')
        items = []
        for row in rows:
            if trace_id is not None and row['id'] not in trace_id:
                continue
            items.append({'trace':store.trace(row['id']), 'job':job_view(row['job_id']) if row['job_id'] else None,
                          'attempts':[{'job':job_view(a['id']),'feedback_history':store.feedback_history(a['id'])} for a in store.item_attempts(run_id,row['id'])]})
        payload = {'schema_version':1,'task':require_task(task_id),'analysis':run,'trajectories':items}
        return Response(json.dumps(payload,ensure_ascii=False,indent=2), media_type='application/json',
                        headers={'Content-Disposition':f'attachment; filename="tianluo-diagnosis-task-{task_id[:8]}-run-{run["number"]}.json"'})

    app.include_router(router)
