"""Run the official AgentDebugX read-only text diagnosis in an isolated process."""

import contextlib
from dataclasses import asdict
import json
import os
import sys

from ..model_headers import merge_headers, parse_headers
from ..prompt_language import normalize_prompt_language
from .agentdebugx_prompts import render_diagnostic_prompt
from .agentdebugx_source import load_vendored_agentdebugx

load_vendored_agentdebugx()

from agentdebug.diagnose.detect.analyzers import HeuristicAnalyzer
from agentdebug.diagnose.profiles.deepdebug import DeepDebugAnalyzer
from agentdebug.diagnose.recover import DeepDebugRecovery
from agentdebug.runtime import OpenAICompatClient, extract_json_block
from agentdebug.schema import AgentTrajectory, DiagnosticReport


class WorkbenchModel(OpenAICompatClient):
    """Apply connection settings and language at the upstream client boundary."""

    def __init__(self, settings):
        super().__init__(
            base_url=settings["base_url"], api_key=os.environ["OPENAI_API_KEY"], model=settings["model"],
            timeout=settings["timeout"], max_retries=settings["max_retries"],
            default_max_tokens=min(8192, settings["max_tokens"]),
        )
        self.configured_headers = parse_headers(os.environ.get("TRACELENS_MODEL_HEADERS"))
        self.language = normalize_prompt_language(settings["prompt_language"])
        self.max_tokens = settings["max_tokens"]
        self.calls = 0
        self.valid_responses = 0

    def complete(self, messages, *, max_tokens=None, timeout=None, **kwargs):
        self.calls += 1
        result = super().complete(
            messages,
            max_tokens=min(max_tokens or self.default_max_tokens, self.max_tokens),
            timeout=min(timeout or self.timeout, self.timeout), **kwargs,
        )
        if extract_json_block(result.text) is not None:
            self.valid_responses += 1
        return result

    def format_diagnostic_prompt(self, key, default, values):
        return render_diagnostic_prompt(self.language, key, default, values)

    def _post(self, url, headers, body, timeout):
        headers = merge_headers(headers, self.configured_headers)
        return super()._post(url, headers, body, timeout)


def run(payload):
    trajectory = AgentTrajectory.model_validate(payload["trajectory"])
    model = WorkbenchModel(payload["settings"])
    detection = HeuristicAnalyzer().analyze(trajectory)
    links = []
    if payload["outcome"] == "succeeded":
        summary = "业务运行成功，无需失败归因。" if model.language == "zh" else "The run succeeded. No failure attribution is needed."
        report = DiagnosticReport(trace_id=trajectory.trace_id, task_id=trajectory.task_id, summary=summary,
                                  metadata={"task_success": True})
    else:
        analysis = DeepDebugAnalyzer(llm=model, prior_findings=detection.findings).analyze(trajectory)
        if not model.valid_responses:
            raise RuntimeError("AgentDebugX received no usable model response")
        report = analysis.report
        if analysis.diagnosis:
            links = [asdict(ref) for ref in analysis.diagnosis.evidence_references]
        proposals = DeepDebugRecovery().suggest(trajectory, report)
        values = [asdict(proposal) for proposal in proposals]
        report.recovery = {"method": "deepdebug", "proposal_count": len(values), "proposals": values,
                           "primary": values[0] if values else None}
    return {"diagnosis": report.model_dump(mode="json"), "detection": detection.model_dump(mode="json"),
            "evidence": links, "usage": {"requests": model.calls, "request_unit": "completion_calls",
                                       "prompt_tokens": model.usage_total.prompt_tokens,
                                       "completion_tokens": model.usage_total.completion_tokens}}


if __name__ == "__main__":
    with contextlib.redirect_stdout(sys.stderr):
        result = run(json.load(sys.stdin))
    print(json.dumps(result, ensure_ascii=False))
