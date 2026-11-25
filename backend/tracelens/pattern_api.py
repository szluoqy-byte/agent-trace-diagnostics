"""Endpoints for reviewing and running failure pattern analysis."""

import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlite3 import IntegrityError
from fastapi.responses import Response
from pydantic import Field, model_validator

from .models import Contract
from .pattern_analysis import digest


class PatternScope(Contract):
    task_id: str | None = None
    run_id: str | None = None
    trace_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode='after')
    def validate_source(self):
        if bool(self.task_id) != bool(self.run_id) or not (self.run_id or self.trace_ids):
            raise ValueError('请选择来源诊断轮次或库内轨迹')
        return self


class PatternPreview(Contract):
    scope: PatternScope
    overrides: dict[str, str] = Field(default_factory=dict, max_length=500)


class PatternSubmit(PatternPreview):
    snapshot_hash: str = Field(min_length=64, max_length=64)
    request_id: UUID


class PatternCreate(PatternSubmit):
    name: str = Field(min_length=1, max_length=100)


class PatternReview(Contract):
    decision: Literal['confirmed', 'suspect']
    notes: str = Field(default='', max_length=2000)


def attach_pattern_routes(app, store):
    router = APIRouter(prefix='/api/pattern-tasks')

    def require_task(task_id):
        task = store.task(task_id)
        if not task or task['kind'] != 'failure_patterns':
            raise HTTPException(404, '失败模式分析任务不存在')
        return task

    def require_run(task_id, run_id):
        run = store.pattern_run(task_id, run_id)
        if not run:
            raise HTTPException(404, '聚合分析记录不存在')
        return run

    def prepare(body):
        try:
            return store.pattern_preview(body.scope.model_dump(exclude_none=True), body.overrides)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    def submit(body, name, task_id=None):
        request_id = str(body.request_id)
        request_hash = digest([task_id, name, body.model_dump(mode='json', exclude={'request_id'})])
        try:
            replay = store.pattern_replay(request_id, request_hash)
            if replay:
                return replay
            preview = prepare(body)
            if preview['snapshot_hash'] != body.snapshot_hash:
                raise HTTPException(409, '来源诊断已变化，请重新确认样本后提交')
            if preview['counts'].get('included', 0) < 2:
                raise HTTPException(422, '至少需要两条有可用归因的失败轨迹')
            return store.save_pattern_run(name, task_id, preview, request_id, request_hash)
        except IntegrityError as exc:
            if exc.sqlite_errorname != 'SQLITE_CONSTRAINT_FOREIGNKEY':
                raise
            raise HTTPException(409, '来源记录已删除，请重新选择轨迹') from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/preview')
    def preview(body: PatternPreview):
        return prepare(body)

    @router.post('')
    def create(body: PatternCreate):
        if not body.name.strip():
            raise HTTPException(422, '请填写任务名称')
        return submit(body, body.name.strip())

    @router.get('/{task_id}')
    def detail(task_id: str):
        return {**require_task(task_id), 'runs': store.pattern_runs(task_id)}

    @router.post('/{task_id}/runs')
    def rerun(task_id: str, body: PatternSubmit):
        task = require_task(task_id)
        runs = store.pattern_runs(task_id)
        if not runs:
            raise HTTPException(404, '失败模式分析任务不存在')
        last = runs[0]
        scope = require_run(task_id, last['id'])['scope']
        if body.scope.task_id != scope.get('task_id') or sorted(set(body.scope.trace_ids or [])) != scope['trace_ids']:
            raise HTTPException(422, '重新分析须使用原任务的样本范围')
        return submit(body, task['name'], task_id)

    @router.get('/{task_id}/runs/{run_id}')
    def result(task_id: str, run_id: str):
        run = require_run(task_id, run_id)
        reviews = {}
        for review in store.pattern_reviews(run_id):
            reviews.setdefault(review['pattern_id'], review)
        return {**run, 'reviews': reviews}

    @router.get('/{task_id}/runs/{run_id}/members')
    def members(task_id: str, run_id: str, pattern_id: str = '', trace_id: str = '',
                status: Literal['all','excluded'] = 'all', page: int = Query(1,ge=1),
                page_size: Literal['10','20','50'] = '10'):
        run = require_run(task_id, run_id)
        items = store.pattern_items(run_id)
        if not items:
            raise HTTPException(404, '失败模式分析记录不存在')
        if pattern_id:
            pattern = next((p for p in run['result']['patterns'] if p['id'] == pattern_id), None)
            if pattern is None:
                raise HTTPException(404, '失败模式不存在')
            by_id = {item['trace_id']: item for item in items}
            items = [by_id[trace] for trace in pattern['trace_ids']]
        if trace_id:
            items = [item for item in items if item['trace_id'] == trace_id]
        if status == 'excluded':
            items = [item for item in items if item['state'] != 'included']
        size = int(page_size)
        pages = max(1, (len(items) + size - 1) // size)
        page = min(page, pages)
        return {'items': items[(page-1)*size:page*size], 'page': page, 'pages': pages, 'total': len(items), 'page_size': size}

    @router.post('/{task_id}/runs/{run_id}/patterns/{pattern_id}/review')
    def review(task_id: str, run_id: str, pattern_id: str, body: PatternReview):
        run = require_run(task_id, run_id)
        if not any(p['id'] == pattern_id for p in run['result']['patterns']):
            raise HTTPException(404, '失败模式不存在')
        try:
            store.add_pattern_review(run, pattern_id, body.decision, body.notes.strip())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'saved': True}

    @router.get('/{task_id}/runs/{run_id}/export')
    def export(task_id: str, run_id: str, format: Literal['json','md','samples'] = 'json'):
        task = require_task(task_id)
        run = require_run(task_id, run_id)
        result = run['result']
        items = store.pattern_items(run_id)
        reviews = store.pattern_reviews(run_id)
        if format == 'md':
            def inline(value):
                return str(value).replace('\r', ' ').replace('\n', ' ')

            lines = [f"# {inline(task['name'])}", '', f"分析轮次 {run['number']}", '',
                     f"可用归因 {result['eligible']}/{result['business_failed']} 条业务失败轨迹",
                     f"重复模式 {result['repeated']} 个", '', "## 模式与代表轨迹", '']
            for pattern in result['patterns']:
                lines.extend([f"- {inline(pattern['category'])} · {inline(pattern['object'])} "
                              f"（{pattern['count']}/{pattern['denominator']}）",
                              f"  - 代表轨迹 ID：{', '.join(pattern['representatives'])}"])
            lines.extend(['', '## 复核记录', ''])
            lines.extend(f"- {review['created_at']} · {review['pattern_id']} · "
                         f"{'已确认' if review['decision']=='confirmed' else '疑似误归组'} · {inline(review['notes'])}"
                         for review in reviews)
            content, media, extension = '\n'.join(lines), 'text/markdown', 'md'
        else:
            if format == 'samples':
                representatives = {}
                for group in result['patterns']:
                    if group['count'] >= 2:
                        for trace_id in group['representatives']:
                            representatives.setdefault(trace_id, []).append(group['id'])
                trajectories = store.pattern_trajectories(sorted(representatives))
                samples = []
                for item in items:
                    if item['trace_id'] in representatives:
                        source = {key: item[key] for key in ('trace_id','source_task_id','source_run_id','job_id','critical_step')}
                        samples.append({**source, 'group_ids': representatives[item['trace_id']],
                                        'trajectory': trajectories[item['trace_id']]})
                payload = {'task_id': task_id, 'run_id': run_id, 'samples': samples}
            else:
                payload = {'task': task,'run': run,'inputs': items,'reviews': reviews}
            content, media, extension = json.dumps(payload, ensure_ascii=False, indent=2), 'application/json', 'json'
        return Response(content, media_type=media, headers={
            'Content-Disposition': f'attachment; filename="failure-patterns-{format}-{run_id[:8]}.{extension}"'})

    app.include_router(router)
