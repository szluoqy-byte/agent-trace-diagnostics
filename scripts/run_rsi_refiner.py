"""Run a label-free critical-step verifier on saved TrajDebug reports."""
import argparse
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from tracelens.config import Settings
from tracelens.model_connections import ModelConnections
from tracelens._vendor.trajdebug.detector.utils.model import APIModel

PROMPT = """You are a critical-step verifier for an agent trajectory diagnosis system.
The trajectory is a failed run. Select the earliest consequential agent step that best explains the task failure.
Use the trajectory content as the source of truth. Official detector outputs are evidence leads and can be incomplete or wrong.
Select an integer step from the trajectory. Do not select user messages, setup-only messages, or a later symptom when an earlier binding error caused it.
A step can be selected when the agent made a binding action, an unsupported claim, or a decision that violated the task or visible evidence.
Return only a JSON object with this schema:
{{"critical_step": integer or null, "confidence": "high"|"medium"|"low", "reason": "short evidence-grounded explanation", "evidence_steps": [integer, ...]}}

=== OFFICIAL DETECTOR LEADS ===
{leads}

=== TRAJECTORY ===
{trajectory}
"""

_tls = threading.local()


def model_for(settings, cache_dir):
    model = getattr(_tls, 'model', None)
    if model is None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        model = APIModel(str(cache_dir / f'{threading.get_ident()}.pkl'), settings.base_url,
                         settings.model, settings.api_key or 'EMPTY')
        _tls.model = model
    return model


def clean_text(value, limit=1800):
    value = str(value or '').replace('\x00', '')
    return value if len(value) <= limit else value[:limit] + '\n...[truncated]'


def make_prompt(trace, report):
    leads = {
        'detector_triggers': [{'step': t.get('step'), 'category': t.get('category'),
                              'taxonomy_tag': t.get('taxonomy_tag'), 'description': t.get('description')}
                             for t in report.get('triggers', [])],
        'detector_instances': [{'id': i.get('id'), 'origin_step': i.get('origin_step'),
                               'qualified_origin_step': i.get('qualified_origin_step'),
                               'object': i.get('object'), 'state': i.get('error_state'),
                               'terminal_relevant': i.get('terminal_relevant')}
                              for i in report.get('instances', [])],
        'detector_attribution': report.get('attribution', {}),
    }
    messages = []
    for m in trace.get('messages', []):
        messages.append({'step': m.get('step'), 'role': m.get('role'), 'judgable': m.get('judgable'),
                         'content': clean_text(m.get('content'))})
    trajectory = {'task_description': clean_text(trace.get('metadata', {}).get('task_description'), 2400),
                  'messages': messages}
    return PROMPT.format(leads=json.dumps(leads, ensure_ascii=False),
                         trajectory=json.dumps(trajectory, ensure_ascii=False))


def parse_output(text):
    if not text:
        raise ValueError('empty verifier response')
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.S)
        if not match:
            raise ValueError('verifier response is not JSON')
        value = json.loads(match.group(0))
    step = value.get('critical_step')
    if step is not None and (type(step) is not int or step < 0):
        raise ValueError('invalid critical_step')
    confidence = value.get('confidence', 'low')
    if confidence not in {'high', 'medium', 'low'}:
        confidence = 'low'
    return {'critical_step': step, 'confidence': confidence,
            'reason': clean_text(value.get('reason'), 1000),
            'evidence_steps': value.get('evidence_steps') if isinstance(value.get('evidence_steps'), list) else []}


def one(path, trace_path, settings, cache_dir):
    trace = json.loads(trace_path.read_text(encoding='utf-8'))
    report = json.loads(path.read_text(encoding='utf-8'))
    model = model_for(settings, cache_dir)
    text, usage = model.generate(make_prompt(trace, report), max_tokens=2048, temperature=0.0,
                                 response_format={'type': 'json_object'}, return_usage=True)
    prediction = parse_output(text)
    prediction.update({'input_sha256': path.stem, 'split': path.parent.name,
                       'report_path': str(path), 'usage': usage or {}})
    return prediction


def trace_path(report):
    run_id = report['provenance']['run_id']
    return Path('data/validation/rsi-round-1/trajdebug/runs') / run_id / 'input.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=('train', 'dev', 'heldout', 'external_eval'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--only-missing', action='store_true', help='仅复核当前没有关键步骤的报告')
    parser.add_argument('--data-dir', type=Path, default=Path('data/validation/rsi-round-1'))
    args = parser.parse_args()
    settings = Settings()
    connection = ModelConnections(settings.data_dir).default()
    if connection:
        settings = replace(settings, **{k: connection[k] for k in ('model', 'base_url', 'api_key')})
    reports = sorted((args.data_dir / 'reports' / args.split).glob('*.json'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    predictions = []
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for path in reports:
            report = json.loads(path.read_text(encoding='utf-8'))
            if args.only_missing and (report.get('attribution') or {}).get('critical_step') is not None:
                continue
            futures[pool.submit(one, path, trace_path(report), settings,
                                args.output.parent / 'cache')] = path
        for future in as_completed(futures):
            path = futures[future]
            try:
                predictions.append(future.result())
                print(args.split, path.stem[:12], 'ok', flush=True)
            except Exception as exc:
                errors.append({'input_sha256': path.stem, 'error': str(exc)})
                print(args.split, path.stem[:12], 'error', str(exc), flush=True)
    predictions.sort(key=lambda x: x['input_sha256'])
    args.output.write_text(json.dumps({'split': args.split, 'model': settings.model,
                                       'predictions': predictions, 'errors': errors}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'split': args.split, 'completed': len(predictions), 'errors': len(errors)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
