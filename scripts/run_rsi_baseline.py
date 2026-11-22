"""Run the frozen, unlabelled RSI inputs through the workbench diagnosis engine."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from tracelens.config import ROOT, Settings
from tracelens.engine import diagnose
from tracelens.evaluation import model_input_digest
from tracelens.llm import ModelError
from tracelens.model_connections import ModelConnections
from tracelens.normalize import parse_upload
from tracelens.trajdebug import UPSTREAM_COMMIT, to_unified, verify_upstream


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def inputs(dataset):
    meta = json.loads((dataset / 'dataset.json').read_text())
    manifest = dataset / 'manifest.jsonl'
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != meta['sha256']['manifest.jsonl']:
        raise ValueError('Manifest hash mismatch')
    cases = []
    groups = defaultdict(set)
    ids = set()
    files = {}
    for row in map(json.loads, manifest.read_text().splitlines()):
        name = row['input_file']
        if name not in files:
            body = (dataset / name).read_bytes()
            if hashlib.sha256(body).hexdigest() != meta['sha256'][name]:
                raise ValueError(name)
            files[name] = parse_upload(body, Path(name).name)
        trace = files[name][row['line'] - 1]
        sha = model_input_digest(to_unified(trace))
        if sha != row['input_sha256'] or sha in ids or trace.annotation:
            raise ValueError(row['id'])
        groups[row['group_id']].add(row['split'])
        ids.add(sha)
        cases.append((row, trace))
    if any(len(parts) != 1 for parts in groups.values()):
        raise ValueError('Task group crosses partitions')
    return cases


async def run(dataset, output, concurrency=None):
    cases = inputs(dataset)
    verify_upstream()
    settings = Settings()
    connection = ModelConnections(settings.data_dir).default()
    if connection:
        settings = replace(settings, **{k: connection[k] for k in ('model', 'base_url', 'api_key')})
    settings = replace(settings, data_dir=output, concurrency=concurrency or settings.concurrency)
    stamp = {
        'commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
        'upstream': UPSTREAM_COMMIT, 'dataset_sha256': hashlib.sha256((dataset/'dataset.json').read_bytes()).hexdigest(),
        'settings': {k: getattr(settings, k) for k in ('model', 'base_url', 'concurrency', 'max_retries', 'llm_concurrency', 'max_tokens', 'timeout', 'cluster_json_mode')},
        'counts': dict(Counter(r['split'] for r, _ in cases)),
    }
    stamp_file = output / 'baseline.json'
    if stamp_file.exists() and json.loads(stamp_file.read_text()) != stamp:
        raise ValueError('Baseline configuration changed')
    write(stamp_file, stamp)
    slots = asyncio.Semaphore(settings.concurrency)
    async def one(row, trace):
        dest = output / 'reports' / row['split'] / (row['input_sha256'] + '.json')
        dest.parent.mkdir(parents=True, exist_ok=True)
        failure = output / 'failures' / row['split'] / dest.name
        if dest.exists() or failure.exists():
            return
        async def progress(stage, percent, partial):
            return None
        async with slots:
            attempts = []
            for attempt in range(2):
                try:
                    report = await diagnose(trace, settings, progress)
                except ModelError as exc:
                    usage = getattr(exc, 'usage', {}) or {}
                    attempts.append({'error': type(exc).__name__, 'usage': usage})
                    print(row['split'], row['input_sha256'][:12],
                          '请求失败，立即重试', flush=True)
                    continue
                write(dest, report)
                print(row['split'], row['input_sha256'][:12], report['usage']['requests'], flush=True)
                return
            failure.parent.mkdir(parents=True, exist_ok=True)
            write(failure, {'attempts': attempts, 'input_sha256': row['input_sha256']})
            print(row['split'], row['input_sha256'][:12], 'failed', flush=True)
    await asyncio.gather(*(one(row, trace) for row, trace in cases))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT/'data/rsi/v1-lite')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--concurrency', type=int, choices=range(1, 9))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'baseline.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(run(args.dataset.resolve(), args.output.resolve(), args.concurrency))
