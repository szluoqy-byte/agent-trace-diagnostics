from contextlib import asynccontextmanager
from typing import Literal
from .engine import ENGINE_VERSION
import json
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError
from .config import Settings, ROOT
from .models import Contract, Review, trajectory_format
from .deepagent import source_bundle, trace_with_source_summary
from .deletion import DeleteSelection
from .normalize import parse_upload
from .execution_view import execution_view
from .store import Store
from .queue import JobQueue
from .trajdebug import UPSTREAM_COMMIT, UPSTREAM_URL
from .reporting import markdown_report
from .official_samples import load_official_samples
from .feedback import feedback_availability
from .execution import RUNTIME_FIELDS, active_execution, job_settings, load_runtime_settings, runtime_snapshot, snapshot
from .diagnosis.synthesis import project_synthesis, review_target
from .diagnosis.review_policy import job_review_status
from .model_settings_api import MODEL_SETTINGS_PATH, attach_model_settings_routes
from .runtime_settings_api import attach_runtime_settings_routes
from .task_api import attach_task_routes
from .pattern_api import attach_pattern_routes


class BatchRequest(Contract):
    trace_ids: list[str] = Field(min_length=1, max_length=50)
    mode: str = "live"


class FeedbackRegeneration(Contract):
    feedback_id: str = Field(min_length=1, max_length=64)


def create_app(settings=None, *, frontend_dir=None):
    settings = settings or Settings()
    store = Store(settings.data_dir)
    store.purge_deleted_artifacts()
    loaded_settings = load_runtime_settings(store, settings)
    for key in RUNTIME_FIELDS:
        setattr(settings, key, getattr(loaded_settings, key))
    execution, connection_id = active_execution(store, settings)
    store.freeze_legacy_runs(snapshot(execution, connection_id))
    queue = JobQueue(store, settings)

    @asynccontextmanager
    async def lifespan(app):
        await queue.start()
        yield
        await queue.stop()

    app = FastAPI(title="天罗智能轨迹诊断 API", version="0.2.0", lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith(MODEL_SETTINGS_PATH):
            return JSONResponse({'detail': '请检查配置名称、接口地址、模型标识和 API 密钥的格式'}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            parsed = urlparse(origin)
            if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                return JSONResponse({"detail": "本地工作台仅接受本地页面的操作请求"}, status_code=403)
        return await call_next(request)

    @app.get("/api/config")
    def config():
        execution, _ = active_execution(store, settings)
        return {"ready": execution.ready, "model": execution.model, "base_url": execution.base_url,
                "has_key": bool(execution.api_key), **runtime_snapshot(settings),
                "engine": ENGINE_VERSION, "algorithm": "TrajDebug",
                "upstream_commit": UPSTREAM_COMMIT, "upstream_url": UPSTREAM_URL}

    @app.get("/api/traces")
    def traces():
        return [{"id": t["id"], "title": t["title"], "created_at": t["created_at"],
                 **{key: t["data"][key] for key in ("dataset", "task_id", "outcome", "kind", "source")},
                 "input_format": trajectory_format(t["data"]),
                 "steps": len(t["data"]["messages"])} for t in store.traces()]

    def require_trace(trace_id):
        trace = store.trace(trace_id)
        if not trace:
            raise HTTPException(404, "轨迹不存在")
        return trace

    def require_job(job_id):
        job = store.job(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    def write(call):
        try:
            return call()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/traces/{trace_id}")
    def trace(trace_id: str):
        return trace_with_source_summary(require_trace(trace_id))

    @app.delete("/api/traces/{trace_id}")
    def delete_trace(trace_id: str):
        try:
            write(lambda: store.delete_trace(trace_id))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"deleted": True}

    @app.post('/api/traces/batch-delete')
    def delete_traces(body: DeleteSelection):
        return store.delete_traces(body.ids)

    @app.get("/api/traces/{trace_id}/execution")
    def trace_execution(trace_id: str):
        return execution_view(require_trace(trace_id)["data"])

    @app.get('/api/trace-library')
    def trace_library(
        page: int = Query(1, ge=1), page_size: Literal['10', '20', '50'] = '10',
        search: str = Query('', max_length=200),
        diagnosis: Literal['all', 'pending', 'queued', 'running', 'completed', 'failed'] = 'all',
        business: Literal['all', 'failed', 'succeeded', 'unknown'] = 'all',
        input_format: Literal['all', 'messages', 'otel_genai', 'deepagent'] = 'all',
        selection: bool = False,
    ):
        return store.trace_library(page, int(page_size), search.strip(), diagnosis, business, input_format,
                                   selection=selection)

    @app.get('/api/trace-library/{trace_id}/history')
    def trace_library_history(trace_id: str, latest_only: bool = False):
        history = store.trace_library_history(trace_id, latest_only=latest_only)
        if history is None:
            raise HTTPException(404, '轨迹不存在')
        return history

    @app.get("/api/traces/{trace_id}/otel")
    def otel_source(trace_id: str):
        data = require_trace(trace_id)["data"]
        provenance = data.get("otel")
        if not provenance:
            raise HTTPException(404, "该轨迹没有 OTel 来源")
        bundle = {**provenance, "message_sources": [{"step": m["step"], "source_refs": m.get("source_refs", [])} for m in data["messages"]]}
        return Response(json.dumps(bundle, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="otel-source-{trace_id}.json"'})

    @app.get("/api/traces/{trace_id}/deepagent")
    def deepagent_source(trace_id: str, download: bool = False):
        data = require_trace(trace_id)["data"]
        if not data.get("deepagent"):
            raise HTTPException(404, "该轨迹尚未保存 DeepAgent 原始 span")
        headers = {"Content-Disposition": f'attachment; filename="deepagent-source-{trace_id}.json"'} if download else None
        return Response(json.dumps(source_bundle(data), ensure_ascii=False, indent=2), media_type="application/json", headers=headers)

    @app.post("/api/import")
    async def import_files(files: list[UploadFile] = File(...), outcome: str = Form("")):
        if outcome not in {"", "failed", "succeeded"}:
            raise HTTPException(422, "运行结果必须为 failed 或 succeeded")
        if len(files) > 50:
            raise HTTPException(413, "每批最多导入 50 个文件")
        parsed, total = [], 0
        try:
            for file in files:
                content = await file.read(10 * 1024 * 1024 + 1)
                total += len(content)
                if total > 20 * 1024 * 1024 or len(content) > 10 * 1024 * 1024:
                    raise HTTPException(413, "单文件最多 10MB，每批最多 20MB")
                parsed.extend(parse_upload(content, file.filename or "trajectory.json", workbench=True))
                if len(parsed) > 50:
                    raise HTTPException(413, "每批最多导入 50 条轨迹")
        except (ValueError, UnicodeDecodeError, ValidationError) as e:
            raise HTTPException(422, str(e)) from e
        if not parsed:
            raise HTTPException(422, "文件中没有轨迹")
        for trajectory in parsed:
            if trajectory.outcome == "unknown" and outcome:
                trajectory.outcome = outcome
        # Validate the entire batch before writing anything.
        return {"trace_ids": [store.add_trace(t)["id"] for t in parsed]}

    @app.post("/api/samples")
    def samples():
        if not settings.enable_validation_data:
            raise HTTPException(404, "当前工作区未启用评测数据导入")
        try:
            parsed = load_official_samples(settings.data_dir / "sources")
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(409, "官方测试集尚未下载或校验失败，请运行 .venv/bin/python scripts/fetch_samples.py") from exc
        return {"trace_ids": [store.add_trace(t)["id"] for t in parsed]}

    @app.get("/api/jobs")
    def jobs():
        rows = []
        for job in store.jobs():
            feedback = store.latest_feedback(job['id'])
            rows.append({**{k: v for k, v in job.items() if k != 'result'},
                         'engine_version': (job.get('result') or {}).get('engine_version'),
                         'feedback': {k: v for k, v in feedback.items() if k != 'result'} if feedback else None})
        return rows

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        current = require_job(job_id)
        eligible, reason = feedback_availability(store, current, job_settings(store, settings, job_id))
        try:
            store.assert_job_writable(job_id)
            read_only = False
        except ValueError:
            read_only = True
        reviews = store.reviews(job_id)
        result = project_synthesis(current['result']) if current.get('result') else current.get('result')
        return {**current, 'result': result, 'reviews': reviews, 'review_status': job_review_status(current, reviews),
                'feedback': store.latest_feedback(job_id),
                'feedback_eligible': eligible and not read_only, 'feedback_unavailable_reason': '历史分析记录只读' if read_only else reason,
                'read_only': read_only}

    @app.post('/api/jobs/{job_id}/feedback')
    def enqueue_feedback(job_id: str):
        current = require_job(job_id)
        write(lambda:store.assert_job_writable(job_id))
        latest = store.latest_feedback(job_id)
        if latest and latest['status'] in {'queued', 'running', 'completed'}:
            return latest
        eligible, reason = feedback_availability(store, current, job_settings(store, settings, job_id))
        if not eligible:
            raise HTTPException(409, reason)
        return write(lambda:store.enqueue_feedback(job_id))

    @app.post('/api/jobs/{job_id}/feedback/regenerate')
    def regenerate_feedback(job_id: str, body: FeedbackRegeneration):
        current = require_job(job_id)
        eligible, reason = feedback_availability(store, current, job_settings(store, settings, job_id))
        if not eligible:
            raise HTTPException(409, reason)
        return write(lambda:store.enqueue_feedback(job_id, regenerate_from=body.feedback_id))

    @app.get('/api/jobs/{job_id}/feedback/export')
    def export_feedback(job_id: str):
        require_job(job_id)
        feedback = store.latest_feedback(job_id)
        if not feedback or feedback['status'] != 'completed':
            raise HTTPException(409, '请等待改进建议生成完成后导出')
        result = feedback['result']
        report = result.get('official_report', result)
        return Response(json.dumps(report, ensure_ascii=False, indent=2), media_type='application/json',
                        headers={'Content-Disposition': f'attachment; filename="tianluo-diagnosis-feedback-{job_id[:8]}.json"'})

    @app.post("/api/jobs")
    def enqueue(body: BatchRequest):
        if body.mode != "live" and not (settings.enable_validation_data and body.mode == "demo"):
            raise HTTPException(422, "无效诊断模式")
        execution, connection_id = active_execution(store, settings)
        if body.mode == "live" and not execution.ready:
            raise HTTPException(409, "尚未配置默认诊断模型，请前往系统设置")
        for trace_id in body.trace_ids:
            trace = require_trace(trace_id)
            if trace["data"]["kind"] == "demo" and not settings.enable_validation_data:
                raise HTTPException(422, "历史合成轨迹仅供查看，请选择待诊断的轨迹")
            if body.mode == "demo" and trace["data"]["kind"] != "demo":
                raise HTTPException(422, "公开和业务轨迹必须使用真实模型诊断")
            if body.mode == "live" and trace["data"]["outcome"] == "unknown":
                raise HTTPException(422, "轨迹缺少业务结果，请重新导入并标记为业务成功或业务失败")
        frozen = snapshot(execution, connection_id) if body.mode == "live" else None
        return {"jobs": [write(lambda: store.enqueue(t, body.mode, config=frozen)) for t in dict.fromkeys(body.trace_ids)]}

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        job = require_job(job_id)
        write(lambda:store.assert_job_writable(job_id))
        if job["status"] != "failed":
            raise HTTPException(409, "仅失败任务可重试")
        if job["mode"] == "demo" and not settings.enable_validation_data:
            raise HTTPException(409, "该历史诊断不可重试")
        execution = job_settings(store, settings, job_id)
        if job["mode"] == "live" and not execution.ready:
            raise HTTPException(409, "原诊断使用的模型配置不可用，请使用当前默认模型重新分析")
        # New job preserves failed-stage artifacts and the audit trail.
        with store.connect() as db:
            mapping = db.execute('SELECT run_id FROM run_attempts WHERE job_id=?', (job_id,)).fetchone()
        if mapping:
            ids = write(lambda:store.enqueue_items(mapping['run_id'], [job['trace_id']], 'retry_failed'))
            if not ids:
                raise HTTPException(409, '该请求已被重试或已不再处于失败状态')
            return store.job(ids[0])
        frozen = (store.job_config(job_id) or snapshot(execution)) if job["mode"] == "live" else None
        return write(lambda: store.enqueue(job["trace_id"], job["mode"], config=frozen, retry_of=job_id))

    @app.post('/api/jobs/{job_id}/regenerate')
    def regenerate(job_id: str):
        current = require_job(job_id)
        trace = require_trace(current['trace_id'])['data']
        if trace['outcome'] == 'unknown':
            raise HTTPException(422, '轨迹缺少业务结果，请先补齐结果')
        if not job_settings(store, settings, job_id).ready:
            raise HTTPException(409, '原诊断使用的模型配置不可用，请使用当前默认模型重新分析')
        new_id = write(lambda:store.regenerate_attempt(job_id))
        return require_job(new_id)

    @app.post("/api/jobs/{job_id}/reviews")
    def review(job_id: str, body: Review):
        job = require_job(job_id)
        write(lambda:store.assert_job_writable(job_id))
        if job["status"] != "completed":
            raise HTTPException(409, "请等待诊断完成后复核")
        if body.decision == "rejected" and body.critical_step is not None:
            raise HTTPException(422, "驳回结论时关键步骤应留空")
        original, algorithm = review_target(job["result"])
        trace = require_trace(job["trace_id"])["data"]
        if body.critical_step is not None:
            invalid_step = body.critical_step >= len(trace["messages"])
            if not invalid_step and algorithm not in {"AgentDebugX", "CHIEF"}:
                message = trace["messages"][body.critical_step]
                invalid_step = message["role"] == "user" or message.get("judgable") is False
            if invalid_step:
                raise HTTPException(422, "关键步骤必须是轨迹中的可诊断消息")
        if body.decision == "confirmed" and any(getattr(body, k) != original[k] for k in ("critical_step", "category", "summary")):
            raise HTTPException(422, "结论有变动时请选择修改结论")
        write(lambda:store.add_review(job_id, body.model_dump()))
        return {"reviews": store.reviews(job_id)}

    @app.get("/api/jobs/{job_id}/artifacts/{stage}")
    def artifact(job_id: str, stage: str):
        job = require_job(job_id)
        result = job.get("result") or {}
        engines = result.get("engine_runs") or {}
        if stage == 'agentdebugx':
            report = result if result.get('algorithm') == 'AgentDebugX' else (engines.get('agentdebugx') or {}).get('report')
            if not report or not report.get('official_output'):
                raise HTTPException(404, 'AgentDebugX 原始报告不存在')
            return JSONResponse({"detection": report.get("engine_detection"), "diagnosis": report["official_output"]},
                                headers={"Content-Disposition": f'attachment; filename="agentdebugx-{job_id}.json"'})
        if stage == 'chief':
            report = result if result.get('algorithm') == 'CHIEF' else (engines.get('chief') or {}).get('report')
            if not report or not report.get('official_output'):
                raise HTTPException(404, 'CHIEF 原始诊断报告不存在')
            return JSONResponse(report["official_output"],
                                headers={"Content-Disposition": f'attachment; filename="chief-{job_id}.json"'})
        run_id = (result.get("provenance", {}).get("run_id")
                  or (engines.get('trajdebug') or {}).get('provenance', {}).get('run_id', ''))
        names = {"stage_a": "input_stage_a.json", "stage_b": "input_stage_b.json", "phase1": "input_stage_c_phase1.json",
                 "phase2": "input_stage_c_phase2.json", "final": "input_final.json"}
        if stage not in names or not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
            raise HTTPException(404, "阶段产物不存在")
        path = settings.data_dir / "trajdebug" / "runs" / run_id / names[stage]
        if not path.is_file():
            raise HTTPException(404, "该阶段尚未产出结果")
        return FileResponse(path, media_type="application/json", filename=names[stage])

    @app.get("/api/jobs/{job_id}/export")
    def export(job_id: str, format: str = "json"):
        job = require_job(job_id)
        if job["status"] != "completed":
            raise HTTPException(409, "请等待诊断完成后导出")
        trace = require_trace(job["trace_id"])
        reviews = store.reviews(job_id)
        feedback = store.latest_feedback(job_id)
        job = {**job, "result": project_synthesis(job["result"])}
        payload = {"trace": trace_with_source_summary(trace), "job": {**job, "review_status": job_review_status(job, reviews)}, "reviews": reviews,
                   "feedback": feedback, "feedback_history": store.feedback_history(job_id)}
        headers = {"Content-Disposition": f'attachment; filename="tianluo-diagnosis-{job_id[:8]}.{format}"'}
        if format == "json":
            return Response(json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json", headers=headers)
        if format != "md":
            raise HTTPException(422, "format 仅支持 json 或 md")
        return Response(markdown_report(trace, job, reviews, feedback), media_type="text/markdown", headers=headers)

    attach_task_routes(app, store, settings, job)
    attach_pattern_routes(app, store)
    attach_model_settings_routes(app, store.model_connections, settings)
    attach_runtime_settings_routes(app, store, settings, queue)

    dist = Path(frontend_dir) if frontend_dir is not None else ROOT / "frontend" / "dist"
    if (dist / 'index.html').is_file() and (dist / 'assets').is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        template_dir = dist / 'templates'
        if not template_dir.is_dir():
            template_dir = ROOT / 'frontend' / 'public' / 'templates'
        if template_dir.is_dir():
            app.mount("/templates", StaticFiles(directory=template_dir), name="templates")
        @app.get("/")
        def index():
            return FileResponse(dist / "index.html")
    return app


app = create_app()
