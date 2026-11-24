"""Run the pinned TrajDebug pipeline in an isolated process."""
import asyncio
import hashlib
import json
import os
import sys
import uuid
from dataclasses import asdict
from .config import ROOT
from .llm import ModelError
from .trajdebug import ENGINE_VERSION, UPSTREAM_COMMIT, present_report, to_unified, verify_upstream


async def diagnose(trace, settings, progress):
    verify_upstream()
    if not settings.ready:
        raise ModelError("请配置模型接口后重启服务")
    unified = to_unified(trace)
    run_id = uuid.uuid4().hex
    directory = settings.data_dir / "trajdebug" / "runs" / run_id
    directory.mkdir(parents=True)
    (directory / "input.json").write_text(json.dumps(unified, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    config = {key: value for key, value in asdict(settings).items()
              if key not in {"api_key", "data_dir", "chunk_chars", "json_mode", "enable_validation_data"}}
    headers = config.pop("headers", {})
    cache_config = {key: value for key, value in config.items() if key != "cluster_json_mode"}
    cache_config["headers"] = headers
    namespace = hashlib.sha256(json.dumps([unified, cache_config, UPSTREAM_COMMIT], sort_keys=True).encode()).hexdigest()
    config["cache_dir"] = str(settings.data_dir / "trajdebug" / "cache" / namespace)
    (directory / "settings.json").write_text(json.dumps(config), encoding="utf-8")
    env = {**os.environ, "OPENAI_API_KEY": settings.api_key or "EMPTY",
           "TRACELENS_MODEL_HEADERS": json.dumps(headers, ensure_ascii=False),
           "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": str(ROOT / "backend") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "tracelens.trajdebug_worker", str(directory),
                                                 env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    partial = {"schema_version": 2, "algorithm": "TrajDebug", "engine_version": ENGINE_VERSION,
               "provenance": {"commit": UPSTREAM_COMMIT, "run_id": run_id}, "stage_metrics": []}
    try:
        while line := await process.stdout.readline():
            if not line.startswith(b"TRACELENS_EVENT "):
                continue
            event = json.loads(line[len(b"TRACELENS_EVENT "):])
            partial["stage_metrics"] = event.get("stage_metrics", [])
            partial["usage"] = event.get("usage", {})
            await progress(event["stage"], event["progress"], partial)
        code = await process.wait()
        if code:
            raise ModelError("诊断执行失败。请检查模型配置和服务状态后重试。", usage=partial.get("usage"))
        payload = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        report = present_report(trace, payload["outputs"], model=settings.model, usage=payload["usage"],
                                run_id=run_id, stage_metrics=payload["stage_metrics"])
        report["execution"] = {key: config[key] for key in ("max_tokens", "llm_concurrency", "max_retries", "timeout", "cluster_json_mode")}
        report["unified_input_sha256"] = hashlib.sha256((directory / "input.json").read_bytes()).hexdigest()
        await progress("诊断完成", 100, report)
        return report
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

