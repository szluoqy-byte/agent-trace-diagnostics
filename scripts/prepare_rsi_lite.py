"""Derive a frozen RSI subset from the full local dataset."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from prepare_rsi_data import Case, VERSION, digest, input_batches, json_line

QUOTAS_100 = {
    'train': {'airline': 17, 'retail': 31, 'swebenchpro': 12},
    'dev': {'airline': 6, 'retail': 6, 'swebenchpro': 3},
    'heldout': {'airline': 6, 'retail': 6, 'swebenchpro': 3},
    'external_eval': {'alfworld': 4, 'gaia': 3, 'webshop': 3},
}
QUOTAS_200 = {
    'train': {'airline': 48, 'retail': 48, 'swebenchpro': 24},
    'dev': {'airline': 9, 'retail': 15, 'swebenchpro': 6},
    'heldout': {'airline': 9, 'retail': 15, 'swebenchpro': 6},
    'external_eval': {'alfworld': 8, 'gaia': 6, 'webshop': 6},
}


def verified_file(source, checksums, relative):
    body = (source / relative).read_bytes()
    if digest(body) != checksums[relative]:
        raise ValueError(f'Mismatch in full dataset: {relative}')
    return body


def input_cost(row, byte_sizes):
    return row['steps'] * byte_sizes[row['id']], row['steps'], row['id']


def select(rows, byte_sizes, quota):
    representatives = {}
    for row in sorted(rows, key=lambda row: input_cost(row, byte_sizes)):
        representatives.setdefault(row['group_id'], row)
    pool = list(representatives.values())
    if len(pool) < quota:
        raise ValueError('Not enough independent task groups for the quota')
    # Keep one higher-cost example in each domain to preserve length coverage.
    upper_quartile = (len(pool) - 1) * 3 // 4
    return pool[:quota - 1] + [pool[max(quota - 1, upper_quartile)]]


def expand_selection(rows, byte_sizes, original, quota):
    chosen_ids = {row['id'] for row in original}
    group_counts = Counter(row['group_id'] for row in original)
    additions = []
    # Rank additional cases by how many members of their group precede them.
    # This covers more task groups before taking another run from the same group.
    for row in sorted(rows, key=lambda row: input_cost(row, byte_sizes)):
        if row['id'] not in chosen_ids:
            group = row['group_id']
            additions.append((group_counts[group], input_cost(row, byte_sizes), row))
            group_counts[group] += 1
    needed = quota - len(original)
    if not 0 <= needed <= len(additions):
        raise ValueError('Not enough source trajectories for the expanded quota')
    return original + [row for _, _, row in sorted(additions)[:needed]]


def build(source, output, size):
    raw_summary = (source / 'dataset.json').read_bytes()
    full = json.loads(raw_summary)
    if full['version'] != VERSION:
        raise ValueError('Expected the full RSI v1 dataset')
    checksums = full['sha256']
    original = [json.loads(line) for line in verified_file(source, checksums, 'manifest.jsonl').splitlines()]
    (output / 'labels').mkdir()
    checks, counts, manifest, groups = {}, {}, [], {}
    quotas = QUOTAS_100 if size == 100 else QUOTAS_200

    for split, quota in quotas.items():
        rows = [row for row in original if row['split'] == split]
        input_files = {row['input_file'] for row in rows}
        payloads = {relative: verified_file(source, checksums, relative).splitlines()
                    for relative in input_files}
        byte_sizes = {row['id']: len(payloads[row['input_file']][row['line'] - 1]) + 1 for row in rows}
        domains = defaultdict(list)
        for row in rows:
            domains[row['domain']].append(row)
        chosen = {}
        for domain, count in quota.items():
            domain_rows = domains[domain]
            original_selection = select(domain_rows, byte_sizes, QUOTAS_100[split][domain])
            selected = original_selection if size == 100 else expand_selection(
                domain_rows, byte_sizes, original_selection, count)
            for row in selected:
                if groups.setdefault(row['group_id'], split) != split or row['id'] in chosen:
                    raise ValueError(f'Cross-split task group or repeated trajectory: {row["id"]}')
                chosen[row['id']] = row

        label_path = f'labels/{split}.jsonl'
        labels = {label['id']: label for label in map(json.loads,
                  verified_file(source, checksums, label_path).splitlines())}
        members = []
        for ident, row in chosen.items():
            data = json.loads(payloads[row['input_file']][row['line'] - 1])
            label = labels[ident]
            if data['metadata']['annotation'] or label['input_sha256'] != row['input_sha256']:
                raise ValueError(f'Mismatched input or label: {ident}')
            case = Case(id=ident, domain=row['domain'], path=row['source_path'], pair_id=row['pair_id'],
                        upstream_blob=row['source_blob_sha1'], upstream_sha256=row['source_sha256'],
                        input=data, annotation=label['source_annotation'], keys=())
            members.append((row['group_id'], case))

        split_labels = []
        for relative, file_hash, batch in input_batches(split, members, output):
            checks[relative] = file_hash
            for row in batch:
                original_row = chosen[row['id']]
                if row['input_sha256'] != original_row['input_sha256']:
                    raise ValueError(f'Model input changed: {row["id"]}')
                split_labels.append(labels[row['id']])
            manifest.extend(batch)
        body = b''.join(map(json_line, split_labels))
        (output / label_path).write_bytes(body)
        checks[label_path] = digest(body)
        counts[split] = {
            'trajectories': len(chosen), 'groups': len({row['group_id'] for row in chosen.values()}),
            'domains': dict(Counter(row['domain'] for row in chosen.values())),
            'steps': sum(row['steps'] for row in chosen.values()),
            'full_steps': sum(row['steps'] for row in rows),
            'input_bytes': sum(byte_sizes[ident] for ident in chosen),
            'full_input_bytes': sum(byte_sizes.values()),
        }

    if len(manifest) != size:
        raise ValueError(f'The lite dataset must contain exactly {size} trajectories')
    manifest_bytes = b''.join(map(json_line, manifest))
    (output / 'manifest.jsonl').write_bytes(manifest_bytes)
    checks['manifest.jsonl'] = digest(manifest_bytes)
    summary = {
        'version': VERSION + ('-lite' if size == 100 else '-lite-200'), 'full_dataset_version': VERSION,
        'full_dataset_sha256': digest(raw_summary),
        'upstream_commit': full['upstream_commit'],
        'counts': counts, 'sha256': checks,
    }
    (output / 'dataset.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/rsi/v1'))
    parser.add_argument('--output', type=Path, default=Path('data/rsi/v1-lite'))
    parser.add_argument('--size', type=int, choices=(100, 200), default=200)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a different directory')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='rsi-lite-build-', dir=args.output.parent) as temporary:
        staged = Path(temporary) / 'dataset'
        staged.mkdir()
        summary = build(args.source, staged, args.size)
        staged.rename(args.output)
    print(json.dumps(summary['counts'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
