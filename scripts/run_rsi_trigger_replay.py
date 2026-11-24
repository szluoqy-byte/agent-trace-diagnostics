"""Replay an external Stage B audit through the upstream Phase 1-3 stages.

This is an evaluation-only experiment. It reads saved official Stage A/B
artifacts, appends label-free audit triggers, and leaves the vendored
TrajDebug implementation untouched.
"""
import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from tracelens.config import Settings
from tracelens.model_connections import ModelConnections
from tracelens.trajdebug import present_report, verify_upstream


def load_audit(path: Path):
    data = json.loads(path.read_text(encoding='utf-8'))
    return {row["input_sha256"]: row.get("additional_triggers", [])
            for row in data.get("predictions", [])}


def stage_config(settings, cache_dir, name):
    config = {
        "base_url": settings.base_url,
        "model": settings.model,
        "api_key": settings.api_key or "EMPTY",
        "cache_url": str(cache_dir / f"{name}.pkl"),
        "max_tokens": settings.max_tokens,
        "llm_concurrency": settings.llm_concurrency,
        "llm_timeout_seconds": settings.timeout,
    }
    if name == "phase1":
        config["judgement_force_json"] = settings.cluster_json_mode
    return config


def augment_stage_b(stage_b: dict, additions: list[dict]) -> dict:
    payload = dict(stage_b)
    original = list(payload.get("step_triggers") or [])
    seen = {(item.get("step"), item.get("taxonomy_tag"), item.get("wrong_content_quote"))
            for item in original}
    for item in additions:
        trigger = {
            "step": item.get("step"),
            "category": item.get("category", ""),
            "taxonomy_tag": item.get("taxonomy_tag", ""),
            "attribution": item.get("attribution", "agent"),
            "wrong_content_quote": item.get("wrong_content_quote", ""),
            "reference_quote": item.get("reference_quote", ""),
            "confidence": item.get("confidence", "low"),
            "confidence_reasoning": item.get("reason", ""),
        }
        key = (trigger["step"], trigger["taxonomy_tag"], trigger["wrong_content_quote"])
        if type(trigger["step"]) is int and key not in seen:
            original.append(trigger)
            seen.add(key)
    payload["step_triggers"] = original
    return payload


async def replay_one(report_path, audit, settings, run_root, output_root, classes):
    report = json.loads(report_path.read_text(encoding='utf-8'))
    run_id = report["provenance"]["run_id"]
    source_dir = run_root / "trajdebug" / "runs" / run_id
    trace_file = source_dir / "input.json"
    stage_a_file = source_dir / "input_stage_a.json"
    stage_b_file = source_dir / "input_stage_b.json"
    stage_b = json.loads(stage_b_file.read_text(encoding='utf-8'))
    augmented_dir = output_root / "runs" / run_id
    augmented_dir.mkdir(parents=True, exist_ok=True)
    augmented_stage_b = augmented_dir / "input_stage_b.json"
    augmented_stage_b.write_text(json.dumps(augment_stage_b(stage_b, audit.get(report_path.stem, [])),
                                            ensure_ascii=False, indent=2), encoding='utf-8')
    cache_dir = output_root / "cache" / report_path.stem
    cache_dir.mkdir(parents=True, exist_ok=True)
    phase1 = classes[0](stage_config(settings, cache_dir, "phase1"))
    try:
        phase1_payload = await phase1.process_file(str(augmented_stage_b), str(augmented_dir))
    finally:
        phase1.close()
    phase1_file = augmented_dir / "input_stage_c_phase1.json"
    phase2 = classes[1](stage_config(settings, cache_dir, "phase2"))
    try:
        phase2_payload = await phase2.process_file(str(phase1_file), str(trace_file), str(augmented_dir))
    finally:
        phase2.close()
    phase2_file = augmented_dir / "input_stage_c_phase2.json"
    phase3 = classes[2](stage_config(settings, cache_dir, "phase3"))
    try:
        final_payload = await phase3.assemble_file(str(phase2_file), str(augmented_dir), trajectory_file=str(trace_file))
    finally:
        phase3.close()
    return {
        "input_sha256": report_path.stem,
        "run_id": run_id,
        "official_triggers": len(stage_b.get("step_triggers") or []),
        "audit_triggers": len(audit.get(report_path.stem, [])),
        "replay_triggers": len(augment_stage_b(stage_b, audit.get(report_path.stem, []))["step_triggers"]),
        "critical_step": (final_payload.get("critical_error") or {}).get("unified_critical_step"),
        "final": final_payload,
    }


async def main_async(args):
    verify_upstream()
    settings = Settings()
    connection = ModelConnections(settings.data_dir).default()
    if connection:
        settings = replace(settings, **{key: connection[key] for key in ("model", "base_url", "api_key")})
    output_root = args.output
    output_root.mkdir(parents=True, exist_ok=True)
    audit = load_audit(args.audit)
    reports = sorted((args.data_dir / "reports" / args.split).glob("*.json"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tracelens" / "_vendor" / "trajdebug" / "detector"))
    from stage_c_phase1_cluster import Phase1Clusterer
    from stage_c_phase2_state import Phase2Annotator
    from stage_c_phase3_assemble import Phase3Assembler
    classes = (Phase1Clusterer, Phase2Annotator, Phase3Assembler)
    semaphore = asyncio.Semaphore(args.workers)

    async def run(path):
        async with semaphore:
            try:
                result = await replay_one(path, audit, settings, args.data_dir, output_root, classes)
                print(args.split, path.stem[:12], result["critical_step"], flush=True)
                return result
            except Exception as exc:
                print(args.split, path.stem[:12], "failed", str(exc), flush=True)
                return {"input_sha256": path.stem, "error": str(exc)}

    results = await asyncio.gather(*(run(path) for path in reports))
    results.sort(key=lambda row: row["input_sha256"])
    output_root.joinpath("predictions.json").write_text(
        json.dumps({"split": args.split, "model": settings.model, "predictions": results},
                   ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    print(json.dumps({"split": args.split, "completed": sum("error" not in row for row in results),
                      "errors": sum("error" in row for row in results)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", required=True, choices=("train", "dev", "heldout", "external_eval"))
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data/validation/rsi-round-1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
