"""Freeze trajectory inputs and separate step labels for diagnosis accuracy evaluation."""
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unicodedata

from tracelens._vendor.trajdebug.data_processing.schema import validate_unified
from tracelens.evaluation import model_input_digest
from tracelens.normalize import normalize, parse_upload
from tracelens.trajdebug import UPSTREAM_COMMIT, to_unified

VERSION = 'tianluo-rsi-v1'
RATIOS = {'train': 0.60, 'dev': 0.20, 'heldout': 0.20}
TRAJERRBENCH = 'data/trajerrbench/'
EXTERNAL = 'data/agentdebugbench/'
MANIFEST = TRAJERRBENCH + 'manifest.jsonl'
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_BATCH_ITEMS = 25
JSONL_ESCAPES = str.maketrans({'\x85': r'\u0085', '\u2028': r'\u2028', '\u2029': r'\u2029'})


def digest(value):
    return hashlib.sha256(value).hexdigest()


def json_line(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')).translate(JSONL_ESCAPES) + '\n').encode()


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def read_sources(archive, tree):
    expected = {entry['path']: entry['sha'] for entry in tree['tree']
                if entry['type'] == 'blob' and (
                    entry['path'] == MANIFEST or
                    entry['path'].startswith((TRAJERRBENCH, EXTERNAL)) and entry['path'].endswith('.json'))}
    if tree.get('truncated') or MANIFEST not in expected:
        raise ValueError('Upstream file tree is incomplete')
    sources = {}
    with tarfile.open(archive, 'r|gz') as bundle:
        for member in bundle:
            root, _, path = member.name.partition('/')
            if path not in expected:
                continue
            if root != 'TrajDebug-' + UPSTREAM_COMMIT or not member.isfile() or path in sources:
                raise ValueError(f'Unexpected archive entry: {member.name}')
            raw = bundle.extractfile(member).read()
            blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if blob != expected[path]:
                raise ValueError(f'Upstream blob mismatch: {path}')
            sources[path] = raw
    if sources.keys() != expected.keys():
        raise ValueError(f'Missing from archive: {sorted(expected.keys() - sources.keys())}')
    return sources, expected


@dataclass(frozen=True)
class Case:
    id: str
    domain: str
    path: str
    pair_id: str | None
    upstream_blob: str
    upstream_sha256: str
    keys: tuple[str, ...]
    input: dict
    annotation: dict

    @property
    def step(self):
        return self.annotation.get('critical_error_step')

    @property
    def scored(self):
        # The live report rejects targets that cannot be selected as a critical step.
        return type(self.step) is int and self.input['messages'][self.step].get('judgable') is not False


def case_from_source(path, raw, blob, *, domain, pair_id=None, english=None):
    original = json.loads(raw)
    validate_unified(original)
    meta = original['metadata']
    if meta['reward'] != 0:
        raise ValueError(f'Expected a failed trajectory: {path}')
    task_id = meta['task_id']
    if english is not None:
        paired = english['metadata']
        if (paired['task_id'], paired['annotation'].get('critical_error_step')) != (
                task_id, meta['annotation'].get('critical_error_step')):
            raise ValueError(f'English counterpart does not share the label: {path}')
        if meta['extra'].get('pair_id') != pair_id or paired['extra'].get('pair_id') != pair_id:
            raise ValueError(f'English counterpart does not share the pair ID: {path}')
    keys = {f'{domain}:description:{normalized(meta["task_description"])}'}
    if english is not None:
        keys.add(f'{domain}:description:{normalized(english["metadata"]["task_description"])}')
    if domain == 'swebenchpro':
        instance = task_id.partition('_instance_')[2]
        if not instance:
            raise ValueError(f'Missing SWE instance ID: {path}')
        keys.add('swebenchpro:instance:' + instance)
    # Use the workspace adapter as the projected input schema. Its model input
    # uses an empty annotation and only the known framework description in extra.
    projected = to_unified(normalize(original))
    projected['title'] = f'{domain} · {Path(path).stem}'
    validate_unified(projected)
    return Case(f'{meta["dataset"]}:{task_id}', domain, path, pair_id, blob, digest(raw),
                tuple(sorted(key for key in keys if not key.endswith(':description:'))),
                projected, meta['annotation'])


def group_cases(cases):
    """Merge matches transitively across the two descriptions and SWE IDs."""
    parent = list(range(len(cases)))
    by_key = {}

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for index, case in enumerate(cases):
        for key in case.keys:
            previous = by_key.setdefault(key, index)
            parent[root(index)] = root(previous)
    groups = defaultdict(list)
    for index, case in enumerate(cases):
        groups[root(index)].append(case)
    return list(groups.values())


def group_id(cases):
    return digest(json_line(sorted(case.id for case in cases)))[:20]


def assign(cases, seen):
    result = defaultdict(list)
    domains = defaultdict(list)
    for group in group_cases(cases):
        identity = group_id(group)
        if any(case.pair_id in seen for case in group):
            result['regression'].extend((identity, case) for case in group if case.scored)
        else:
            eligible = [case for case in group if case.scored]
            if eligible:
                domains[group[0].domain].append((identity, eligible))
    for domain, groups in sorted(domains.items()):
        count = sum(len(group) for _, group in groups)
        assigned = Counter()
        for identity, group in sorted(groups, key=lambda pair: (-len(pair[1]), digest(json_line([VERSION, pair[0]])))):
            split = max(RATIOS, key=lambda name: RATIOS[name] * count - assigned[name])
            result[split].extend((identity, case) for case in group)
            assigned[split] += len(group)
    return result


def input_batches(split, group_members, target):
    folder = target / 'inputs' / split
    folder.mkdir(parents=True)
    batch, size, index = [], 0, 0

    def flush(rows, batch_index):
        relative = f'inputs/{split}/batch-{batch_index:02d}.jsonl'
        payload = b''.join(row[2] for row in rows)
        (target / relative).write_bytes(payload)
        imported = parse_upload(payload, Path(relative).name)
        expected = [row[1].input['metadata']['task_id'] for row in rows]
        if [trace.task_id for trace in imported] != expected:
            raise ValueError(f'Workspace import did not round-trip {relative}')
        records = []
        for line, (trace, (group, case, _)) in enumerate(zip(imported, rows), 1):
            unified = to_unified(trace)
            if trace.annotation or trace.outcome != 'failed' or unified != {
                    key: case.input[key] for key in ('messages', 'metadata')}:
                raise ValueError(f'Workspace import changed {relative}:{line}')
            records.append({
                'id': case.id, 'group_id': group, 'domain': case.domain, 'source_path': case.path,
                'source_blob_sha1': case.upstream_blob, 'source_sha256': case.upstream_sha256,
                'pair_id': case.pair_id, 'split': split, 'input_file': relative, 'line': line,
                'steps': len(case.input['messages']), 'input_sha256': model_input_digest(unified),
            })
        return relative, digest(payload), records

    for group, case in sorted(group_members, key=lambda item: (item[1].domain, item[0], item[1].id)):
        line = json_line(case.input)
        if len(line) > MAX_BATCH_BYTES:
            raise ValueError(f'Trajectory exceeds safe upload size: {case.path}')
        if batch and (size + len(line) > MAX_BATCH_BYTES or len(batch) >= MAX_BATCH_ITEMS):
            index += 1
            yield flush(batch, index)
            batch, size = [], 0
        batch.append((group, case, line))
        size += len(line)
    if batch:
        yield flush(batch, index + 1)


def build(sources, blobs, prior, output):
    release = [json.loads(line) for line in sources[MANIFEST].splitlines() if line.strip()]
    seen = {item['pair_id'] for item in prior['cases']} | {prior['selection']['excluded_development_pair']}
    if len(release) != len({pair['pair_id'] for pair in release}) or not seen <= {p['pair_id'] for p in release}:
        raise ValueError('Upstream pair identifiers do not match the pinned regression catalog')
    internal = []
    for pair in release:
        path = TRAJERRBENCH + pair['zh_path']
        domain = pair['task_id'].split('_failed_', 1)[0] if pair['source_benchmark'] == 'tau2bench' else 'swebenchpro'
        if domain not in {'airline', 'retail', 'swebenchpro'}:
            raise ValueError(f'Unexpected domain: {domain}')
        internal.append(case_from_source(path, sources[path], blobs[path], domain=domain, pair_id=pair['pair_id'],
                                         english=json.loads(sources[TRAJERRBENCH + pair['en_path']])))
    external = [case_from_source(path, sources[path], blobs[path], domain=path.split('/')[2])
                for path in sorted(sources) if path.startswith(EXTERNAL)]
    cases = internal + external
    if len(cases) != len({case.id for case in cases}):
        raise ValueError('Duplicate dataset/task identifier')
    splits = assign(internal, seen)
    splits['external_eval'] = [(group_id(group), case) for group in group_cases(external)
                               for case in group if case.scored]
    groups, used, content_splits, files, manifest, counts = {}, set(), {}, {}, [], {}
    (output / 'labels').mkdir()
    for split in ('train', 'dev', 'heldout', 'external_eval', 'regression'):
        rows = splits[split]
        if not rows:
            raise ValueError(f'Empty split: {split}')
        lookup = {case.id: case for _, case in rows}
        labels = []
        counts[split] = {'trajectories': len(rows), 'groups': len({group for group, _ in rows}),
                         'domains': dict(sorted(Counter(case.domain for _, case in rows).items()))}
        for relative, file_hash, batch in input_batches(split, rows, output):
            files[relative] = file_hash
            for item in batch:
                ident, group = item['id'], item['group_id']
                case = lookup[ident]
                content_hash = digest(json_line([case.input['metadata']['task_description'], case.input['messages']]))
                if groups.setdefault(group, split) != split or ident in used or (
                        content_splits.setdefault(content_hash, split) != split):
                    raise ValueError(f'Contamination or duplicate input: {ident}')
                used.add(ident)
                labels.append({'id': ident, 'task_id': case.input['metadata']['task_id'],
                               'dataset': case.input['metadata']['dataset'],
                               'input_sha256': item['input_sha256'], 'critical_error_step': case.step,
                               'source_annotation': case.annotation})
            manifest.extend(batch)
        relative = f'labels/{split}.jsonl'
        data = b''.join(map(json_line, labels))
        (output / relative).write_bytes(data)
        files[relative] = digest(data)
    excluded = [{'id': case.id, 'source_path': case.path,
                 'reason': 'missing_reference_step' if case.step is None else 'reference_step_not_judgable'}
                for case in cases if not case.scored]
    if used | {item['id'] for item in excluded} != {case.id for case in cases}:
        raise ValueError('Some source cases were not accounted for')
    (output / 'manifest.jsonl').write_bytes(b''.join(map(json_line, manifest)))
    files['manifest.jsonl'] = digest((output / 'manifest.jsonl').read_bytes())
    metadata = {'version': VERSION, 'upstream_commit': UPSTREAM_COMMIT,
                'upstream_manifest_sha256': digest(sources[MANIFEST]),
                'internal_target_ratios': RATIOS, 'counts': counts, 'excluded': excluded, 'sha256': files}
    (output / 'dataset.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--tree', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('data/rsi/v1'))
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a new directory to preserve the frozen split')
    tree = json.loads(args.tree.read_text())
    prior = json.loads(Path(__file__).with_name('official_testset.json').read_text())
    if prior['commit'] != UPSTREAM_COMMIT:
        parser.error('Regression catalog is pinned to a different upstream commit')
    sources, blobs = read_sources(args.archive, tree)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='rsi-build-', dir=args.output.parent) as tmp:
        staged = Path(tmp) / 'dataset'
        staged.mkdir()
        metadata = build(sources, blobs, prior, staged)
        staged.rename(args.output)
    print(json.dumps({'output': str(args.output), 'counts': metadata['counts'], 'excluded': metadata['excluded']},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
