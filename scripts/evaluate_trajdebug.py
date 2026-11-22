"""Score a saved workbench job offline. Does not call a model."""
import argparse
import json
from pathlib import Path
from tracelens.config import Settings
from tracelens.store import Store
from tracelens.models import Trajectory
from tracelens.evaluation import check_evidence, model_input_digest, stage_scores
from tracelens.trajdebug import to_unified


def reference_step(path, trace):
    with path.open(encoding='utf-8') as stream:
        matches = [row for row in map(json.loads, filter(str.strip, stream))
                   if (row['dataset'], row['task_id']) == (trace.dataset, trace.task_id)]
    if len(matches) != 1:
        raise ValueError('标注文件中没有唯一匹配的数据集和任务编号')
    record = matches[0]
    if record['input_sha256'] != model_input_digest(to_unified(trace)):
        raise ValueError('轨迹内容与标注清单不匹配')
    return record.get('critical_error_step')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--labels", type=Path, help="独立的关键步骤标注 JSONL")
    args = parser.parse_args()
    store = Store(Settings().data_dir)
    job = store.job(args.job_id)
    if not job or job["status"] != "completed":
        parser.error("任务不存在或尚未完成")
    trace = Trajectory.model_validate(store.trace(job["trace_id"])["data"])
    try:
        gold = reference_step(args.labels, trace) if args.labels else trace.annotation.get("critical_error_step")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f'无法关联标注文件：{exc}')
    if type(gold) is not int:
        parser.error("该轨迹没有可评分的 critical_error_step 标注")
    report = job["result"]
    result = {"scope": args.labels.stem if args.labels else "development_smoke_not_heldout",
              "job_id": job["id"], "engine": report["engine_version"],
              "gold_step": gold, **stage_scores(report, gold), "evidence": check_evidence(trace, report),
              "usage": report["usage"], "warnings": report.get("warnings", [])}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
