import asyncio
import logging
from .demo import demo_report
from .diagnosis import LEGACY_DEFAULT_MODE, MODE_ENGINES
from .diagnosis.agentdebugx import diagnose_agentdebugx
from .diagnosis.chief import diagnose_chief
from .diagnosis.compare import diagnose_dual
from .engine import diagnose
from .feedback import generate_feedback, feedback_availability
from .execution import job_settings, run_settings
from .models import Trajectory

logger = logging.getLogger(__name__)


class JobQueue:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.tasks = []
        self.started = False

    async def start(self):
        self.store.recover()
        self.schedule_feedback()
        self.started = True
        self.tasks = [asyncio.create_task(self.worker(slot)) for slot in range(self.settings.concurrency)]

    async def stop(self):
        self.started = False
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []

    async def apply_settings(self):
        """Apply a new concurrency limit without interrupting active jobs."""
        if not self.started:
            return
        self.tasks = [task for task in self.tasks if not task.done()]
        while len(self.tasks) < self.settings.concurrency:
            self.tasks.append(asyncio.create_task(self.worker(len(self.tasks))))

    async def worker(self, slot):
        while True:
            if slot >= self.settings.concurrency:
                return
            job = self.store.claim()
            if not job:
                feedback = self.store.claim_feedback()
                if feedback:
                    await self.feedback(feedback)
                    continue
                await asyncio.sleep(0.3)
                continue
            async def progress(stage, value, partial):
                self.store.update(job["id"], stage=stage, progress=value, result=partial)
            execution = self.settings
            try:
                trace = Trajectory.model_validate(self.store.trace(job["trace_id"])["data"])
                if job["mode"] == "demo":
                    if not self.settings.enable_validation_data:
                        raise ValueError("该历史诊断不可执行")
                    if trace.kind != "demo":
                        raise ValueError("演示模式仅适用于内置合成样例")
                    for stage, value in [("加载演示轨迹", 10), ("展示触发证据", 40), ("展示错误实例", 70), ("展示归因结果", 90)]:
                        await progress(stage, value, {})
                        await asyncio.sleep(0.25)
                    report = demo_report()
                else:
                    config = self.store.job_config(job['id']) or {}
                    execution = run_settings(self.store, self.settings, config)
                    mode = config.get("diagnosis_mode", LEGACY_DEFAULT_MODE)
                    engines = MODE_ENGINES[mode]
                    fresh = self.store.uses_fresh_diagnosis_cache(job['id'])
                    if len(engines) > 1:
                        report = await diagnose_dual(trace, execution, progress, diagnose, mode=mode, fresh_cache=fresh)
                    elif engines[0] == "agentdebugx":
                        report = await diagnose_agentdebugx(trace, execution, progress)
                    elif engines[0] == "chief":
                        report = await diagnose_chief(trace, execution, progress)
                    elif fresh:
                        report = await diagnose(trace, execution, progress, fresh_cache=True)
                    else:
                        report = await diagnose(trace, execution, progress)
                self.store.update(job["id"], status="completed", stage="诊断完成", progress=100, result=report)
                self.schedule_feedback()
            except asyncio.CancelledError:
                self.store.update(job["id"], status="failed", stage="运行中断", error="服务停止，任务可重试")
                raise
            except Exception as exc:
                # Never persist provider payloads, authorization headers, or unknown exception text.
                from .llm import ModelError
                message = str(exc) if isinstance(exc, (ModelError, ValueError)) else "诊断运行失败，请检查服务日志后重试"
                if execution.api_key:
                    message = message.replace(execution.api_key, "[redacted]")
                fields = {}
                if isinstance(exc, ModelError) and exc.issues:
                    partial = self.store.job(job['id']).get('result') or {}
                    fields['result'] = {**partial, 'contract_issues': exc.issues,
                                        'usage': exc.usage or partial.get('usage'),
                                        'model_validation': exc.validation_events or partial.get('model_validation', [])}
                elif isinstance(exc, ModelError) and exc.usage:
                    partial = self.store.job(job['id']).get('result') or {}
                    fields['result'] = {**partial, 'usage': exc.usage}
                self.store.update(job["id"], status="failed", stage="诊断失败", error=message, **fields)
                logger.warning("Diagnosis job %s failed (%s)", job["id"], type(exc).__name__)

    async def feedback(self, feedback):
        execution = self.settings
        try:
            execution = job_settings(self.store,self.settings,feedback['job_id'])
            result = await generate_feedback(self.store.job(feedback['job_id']), execution, feedback['id'])
            self.store.update_feedback(feedback['id'], status='completed', result=result)
        except asyncio.CancelledError:
            self.store.update_feedback(feedback['id'], status='failed', error='服务停止，建议生成已中断，可重试')
            raise
        except Exception as exc:
            from .llm import ModelError
            message = str(exc) if isinstance(exc, (ModelError, ValueError)) else '建议生成失败，请检查模型配置后重试'
            if execution.api_key:
                message = message.replace(execution.api_key, '[redacted]')
            self.store.update_feedback(feedback['id'], status='failed', error=message,
                                       result={'usage': exc.usage} if isinstance(exc, ModelError) and exc.usage else None)
            logger.warning('Feedback %s failed (%s)', feedback['id'], type(exc).__name__)

    def schedule_feedback(self):
        for job_id in self.store.auto_feedback_jobs():
            job = self.store.job(job_id)
            if not job:
                continue
            execution = job_settings(self.store,self.settings,job_id)
            if feedback_availability(job,execution)[0]:
                try:
                    self.store.enqueue_feedback(job_id)
                except ValueError:
                    pass  # The diagnosis may have changed before scheduling.

