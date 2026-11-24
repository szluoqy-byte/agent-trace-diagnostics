"""Summarize stage-level accuracy losses from a completed RSI round."""
import argparse
import json
from collections import Counter
from pathlib import Path

from run_rsi_baseline import inputs
from tracelens.evaluation import stage_scores

PARTS = ('train', 'dev', 'heldout', 'external_eval')


def labels(dataset, split):
    return {row['input_sha256']: row['critical_error_step']
            for row in map(json.loads, (dataset / 'labels' / f'{split}.jsonl').read_text(encoding='utf-8').splitlines())}


def summarize(dataset, reports, split):
    gold = labels(dataset, split)
    counts = Counter()
    total = 0
    for row, _trace in inputs(dataset):
        if row['split'] != split:
            continue
        report = json.loads((reports / 'reports' / split / f"{row['input_sha256']}.json").read_bytes())
        target = gold[row['input_sha256']]
        score = stage_scores(report, target)
        total += 1
        counts.update({key: int(score[key]) for key in
                       ('exact_match', 'within_1', 'within_2', 'trigger_recall',
                        'origin_retained', 'candidate_retained', 'unknown_prediction')})
        if score['exact_match']:
            continue
        prediction = score['prediction_step']
        triggered = any(item.get('step') == target for item in report.get('triggers', []))
        origins = {item.get('qualified_origin_step', item.get('origin_step'))
                   for item in report.get('instances', [])}
        candidates = {item.get('qualified_origin_step', item.get('origin_step'))
                      for item in report.get('instances', []) if item.get('terminal_relevant')}
        if prediction is None:
            counts['no_prediction'] += 1
        elif not triggered:
            counts['trigger_or_downstream_miss'] += 1
        elif target not in origins:
            counts['phase1_origin_miss'] += 1
        elif target not in candidates:
            counts['phase2_candidate_miss'] += 1
        else:
            counts['phase3_selection_miss'] += 1
    return {'n': total, 'metrics': dict(counts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path('data/rsi/v1-lite'))
    parser.add_argument('--reports', type=Path, default=Path('data/validation/rsi-round-1'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'splits': {split: summarize(args.dataset, args.reports, split) for split in PARTS}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
