"""Offline sample scoring. Gold labels must never be supplied to diagnose()."""
import hashlib
import json
from .models import Trajectory


def model_input_digest(unified: dict) -> str:
    body = json.dumps(unified, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(body).hexdigest()


def source_step_to_unified(trace: Trajectory, source_step: int) -> int:
    matches = [m.step for m in trace.messages if m.source_step == str(source_step)]
    if len(matches) != 1:
        raise ValueError(f"源步骤 {source_step} 无法唯一映射为统一消息索引")
    return matches[0]


def agentrx_gold(trace: Trajectory, annotations: list[dict]) -> dict:
    record = next((r for r in annotations if str(r["trajectory_id"]) == trace.task_id), None)
    if record is None:
        raise ValueError(f"AgentRx 标注中没有任务 {trace.task_id}")
    root_id = record["root_cause"]["failure_id"]
    root = next(f for f in record["failures"] if f["failure_id"] == root_id)
    return {"step": source_step_to_unified(trace, root["step_number"]),
            "source_step": root["step_number"], "category": root["failure_category"],
            "reason": root["step_reason"]}


def check_evidence(trace: Trajectory, report: dict) -> dict:
    counts = {"total": 0, "matched": 0, "original_matched": 0, "compressed_matched": 0, "invalid": []}
    def visit(value):
        if isinstance(value, dict):
            if "quote" in value and ("step" in value or "source_id" in value):
                counts["total"] += 1
                step, quote = value.get("step"), value["quote"]
                source = next((s for s in trace.runtime_inputs if s.source_id == value.get("source_id")), None)
                task_source = value.get("source_id") in {"task_description", "agent_framework_description"} and step is None
                valid = (type(step) is int and 0 <= step < len(trace.messages) and value.get("source_id") is None) or (step is None and source is not None) or task_source
                valid = valid and isinstance(quote, str) and bool(quote)
                if valid:
                    content = (trace.messages[step].content if step is not None else
                               trace.agent_framework_description if value.get("source_id") == "agent_framework_description" else
                               trace.task_description if task_source else source.content)
                    valid = quote in content
                    if value.get("start") is not None or value.get("end") is not None:
                        start, end = value.get("start"), value.get("end")
                        if end is None and type(start) is int:
                            end = start + len(quote)
                        valid = valid and type(start) is int and type(end) is int and 0 <= start < end <= len(content) and content[start:end] == quote
                compressed = value.get("source_kind") == "compressed"
                if compressed:
                    valid = False
                    for location in value.get("view_matches", []):
                        source_step, tier = location.get("step"), location.get("tier")
                        text = report.get("compression_views", {}).get(str(source_step), {}).get(tier, "")
                        start, end = location.get("start"), location.get("end")
                        if (type(source_step) is int and 0 <= source_step < len(trace.messages) and source_step == step
                                and type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                                and isinstance(quote, str) and text[start:end] == quote):
                            valid = True
                            break
                if valid:
                    counts["matched"] += 1
                    counts["compressed_matched" if compressed else "original_matched"] += 1
                else:
                    counts["invalid"].append({"step": step, "source_id": value.get("source_id"), "quote": quote})
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    if report.get("evidence_protocol"):
        visit([t[key] for t in report.get("triggers", []) for key in ("commitment", "reference")])
        visit([e for item in report.get("instances", []) for e in item.get("lifecycle_evidence", [])])
    else:
        visit(report)
    return counts


def score_prediction(prediction: int | None, gold: int) -> dict:
    if type(gold) is not int or gold < 0:
        raise ValueError("人工关键步骤必须是非负整数")
    distance = abs(prediction - gold) if type(prediction) is int and prediction >= 0 else None
    return {"exact_match": distance == 0, "absolute_step_distance": distance,
            "within_1": distance is not None and distance <= 1,
            "within_2": distance is not None and distance <= 2}


def verify_input_hash(trace: Trajectory, report: dict) -> bool:
    digest = hashlib.sha256(trace.input_json().encode()).hexdigest()
    return digest == report.get("input_sha256")


def stage_scores(report: dict, gold: int) -> dict:
    instances = report.get('instances', [])
    candidates = [i for i in instances if i['terminal_relevant'] and
                  (report.get('engine_version') != 'fusion-0.1' or i['status'] == 'unresolved')]
    prediction = (report.get('attribution') or {}).get('critical_step')
    proposals = report.get('proposals', report.get('triggers', []))
    def origin(item):
        return item.get('qualified_origin_step') if 'qualified_origin_step' in item else item['origin_step']
    return {'proposal_recall': any(p['step'] == gold for p in proposals),
            'trigger_recall': any(t['step'] == gold for t in report.get('triggers', [])),
            'origin_retained': any(origin(i) == gold for i in instances),
            'candidate_retained': any(origin(i) == gold for i in candidates),
            'candidate_origins': [origin(i) for i in candidates], 'candidate_count': len(candidates),
            'prediction_step': prediction, 'unknown_prediction': prediction is None,
            **score_prediction(prediction, gold)}
