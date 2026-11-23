"""Run unmodified upstream stages; capture transport usage and stage artifacts."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from .trajdebug import STAGES, VENDOR, verify_upstream
from .model_headers import apply_headers, parse_headers
from .prompt_language import localize_model


class Usage:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
                     "cache_hits": 0, "failed_requests": 0, "empty_responses": 0}

    def add(self, **values):
        with self.lock:
            for key, value in values.items():
                self.data[key] += value

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def attach(self, model, config):
        model.client = model.client.with_options(timeout=config["timeout"], max_retries=config["max_retries"])
        def request(_request):
            self.add(requests=1)
        def response(reply):
            if reply.status_code >= 400:
                self.add(failed_requests=1)
                return
            reply.read()
            try:
                usage = reply.json().get("usage") or {}
                self.add(prompt_tokens=int(usage.get("prompt_tokens") or 0), completion_tokens=int(usage.get("completion_tokens") or 0),
                         reasoning_tokens=int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or usage.get("reasoning_tokens") or 0))
            except (ValueError, TypeError, AttributeError):
                pass
        model.http_client.event_hooks = {"request": [request], "response": [response]}
        original = model.cache.check_prompt
        def cache(key):
            value = original(key)
            if value is not None:
                self.add(cache_hits=1)
            return value
        model.cache.check_prompt = cache
        for name in ("generate", "generate_chat"):
            def wrap(*args, _call=getattr(model, name), **kwargs):
                result = _call(*args, **kwargs)
                return_usage = kwargs.get("return_usage", args[4] if len(args) > 4 else False)
                text = result[0] if return_usage else result
                if not text:
                    self.add(empty_responses=1)
                return result
            setattr(model, name, wrap)


async def run(directory):
    verify_upstream()
    sys.path.insert(0, str(VENDOR / "detector"))
    from stage_a_diagnosis import StageACompressor
    from stage_b_per_step import StageBDetector
    from stage_c_phase1_cluster import Phase1Clusterer
    from stage_c_phase2_state import Phase2Annotator
    from stage_c_phase3_assemble import Phase3Assembler
    config = json.loads((directory / "settings.json").read_text())
    cache = Path(config["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    usage = Usage()
    metrics, outputs = [], {}
    stream = sys.stdout
    def event(name, percent):
        stream.write("TRACELENS_EVENT " + json.dumps({"stage": name, "progress": percent, "usage": usage.snapshot(), "stage_metrics": metrics}, ensure_ascii=False) + "\n")
        stream.flush()
    classes = [StageACompressor, StageBDetector, Phase1Clusterer, Phase2Annotator, Phase3Assembler]
    input_file = str(directory / "input.json")
    paths = {"stage_a": directory / "input_stage_a.json", "stage_b": directory / "input_stage_b.json",
             "phase1": directory / "input_stage_c_phase1.json", "phase2": directory / "input_stage_c_phase2.json",
             "final": directory / "input_final.json"}
    current_progress = 0
    try:
        for (key, name, start, end), cls in zip(STAGES, classes):
            current_progress = start
            event(name, start)
            cache_name = key + ("-json" if config.get("cluster_json_mode") else "-text") if key == "phase1" else key
            stage_config = {"base_url": config["base_url"], "model": config["model"], "api_key": os.environ["OPENAI_API_KEY"],
                            "cache_url": str(cache / (cache_name + ".pkl")), "max_tokens": config["max_tokens"],
                            "llm_concurrency": config["llm_concurrency"], "llm_timeout_seconds": config["timeout"]}
            if key == "phase1":
                # Upstream exposes this switch. Its clustering prompt has no
                # 'json' keyword, which some compatible JSON-mode APIs require.
                stage_config["judgement_force_json"] = config.get("cluster_json_mode", False)
            before, started = usage.snapshot(), time.monotonic()
            with contextlib.redirect_stdout(sys.stderr):
                stage = cls(stage_config)
                model = getattr(stage, "compress_model", None) or stage.model
                apply_headers(model, parse_headers(os.environ.get("TRACELENS_MODEL_HEADERS")))
                localize_model(model, config.get("prompt_language", "en"))
                usage.attach(model, config)
                try:
                    if key == "stage_a":
                        payload = await stage.process_file(input_file, str(directory))
                    elif key == "stage_b":
                        payload = await stage.process_file(str(paths["stage_a"]), input_file, str(directory))
                    elif key == "phase1":
                        payload = await stage.process_file(str(paths["stage_b"]), str(directory))
                    elif key == "phase2":
                        payload = await stage.process_file(str(paths["phase1"]), input_file, str(directory))
                    else:
                        payload = await stage.assemble_file(str(paths["phase2"]), str(directory), trajectory_file=input_file)
                finally:
                    stage.close()
            if payload is None:
                raise RuntimeError("Upstream stage returned no output")
            after = usage.snapshot()
            delta = {k: after[k] - before[k] for k in after}
            metrics.append({"id": key, "name": name, "seconds": round(time.monotonic() - started, 2), **delta})
            outputs[key] = payload
            if delta["empty_responses"] and delta["requests"] and not delta["completion_tokens"]:
                raise RuntimeError("No usable model responses in upstream stage")
            event(name, end)
        (directory / "result.json").write_text(json.dumps({"outputs": outputs, "usage": usage.snapshot(), "stage_metrics": metrics}, ensure_ascii=False))
    except Exception as exc:
        event("诊断失败", current_progress)
        (directory / "failure.json").write_text(json.dumps({"failure_type": type(exc).__name__, "completed_stages": list(outputs), "usage": usage.snapshot(), "stage_metrics": metrics}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(Path(sys.argv[1]))))
