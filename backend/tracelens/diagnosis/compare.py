"""Orchestrate independent engine runs and report workbench evidence alignment."""

import asyncio
import logging
from time import monotonic

from ..llm import ModelError
from .agentdebugx import diagnose_agentdebugx


logger = logging.getLogger(__name__)
FAILURE = "该引擎诊断失败，请检查模型连接后重试。"


def root_evidence(report):
    root = (report.get("attribution") or {}).get("critical_step")
    references = [*report.get("evidence", [])]
    references.extend(trigger["commitment"] for trigger in report.get("triggers", []) if trigger.get("commitment"))
    references.extend(item for row in report.get("instances", []) for item in row.get("lifecycle_evidence", []))
    return [item for item in references if root is not None and item.get("step") == root and item.get("original_matched")]


def compare_results(trace, runs):
    if any(run["status"] != "completed" for run in runs.values()):
        return "incomplete"
    if trace.outcome == "succeeded":
        return "succeeded"
    reports = [row["report"] for row in runs.values()]
    steps = [(report.get("attribution") or {}).get("critical_step") for report in reports]
    if any(step is None for step in steps):
        return "unresolved"
    if len(set(steps)) > 1:
        return "different"
    return "aligned" if all(root_evidence(report) for report in reports) else "same_step"


async def diagnose_dual(trace, settings, progress, run_standard, *, fresh_cache=False):
    names = ("trajdebug", "agentdebugx")
    progress_by_engine = dict.fromkeys(names, 0)
    latest = {name: {"status": "running"} for name in names}

    async def publish():
        completed_percent = int(sum(progress_by_engine.values()) / len(names))
        await progress("双路诊断", min(completed_percent, 99), {
            "engine_runs": {name: {key: value for key, value in row.items() if key != "report"}
                            for name, row in latest.items()},
        })

    async def execute(name, run, **options):
        started = monotonic()

        async def update(stage, percent, report):
            progress_by_engine[name] = percent
            latest[name].update(stage=stage, progress=percent)
            if provenance := report.get("provenance"):
                latest[name]["provenance"] = provenance
            await publish()

        try:
            report = await run(trace, settings, update, **options)
            latest[name].update(status="completed", report=report, progress=100)
        except Exception as exc:
            logger.warning("Diagnosis engine %s failed (%s)", name, type(exc).__name__)
            latest[name].update(status="failed", error=FAILURE, progress=100)
        latest[name]["seconds"] = round(monotonic() - started, 2)
        progress_by_engine[name] = 100
        await publish()

    await asyncio.gather(
        execute("trajdebug", run_standard, **({"fresh_cache": True} if fresh_cache else {})),
        execute("agentdebugx", diagnose_agentdebugx),
    )
    primary = next((name for name in names if latest[name]["status"] == "completed"), None)
    if primary is None:
        raise ModelError("两路诊断均失败，请检查模型连接后重试。")
    result = dict(latest[primary]["report"])
    result.update(
        comparison={"state": compare_results(trace, latest), "primary": primary},
        engine_runs={name: {key: value for key, value in row.items() if key != "report" or name != primary}
                     for name, row in latest.items()},
    )
    return result
