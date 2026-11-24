"""Paired evaluation of two frozen, label-free attribution policies on RSI v1-lite."""
import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from run_rsi_baseline import inputs, write
from tracelens.evaluation import score_prediction, stage_scores

POLICIES = ('unsupported_pick', 'supported_chain')
PARTS = ('train', 'dev', 'heldout', 'external_eval')


def predict(policy, report, trace):
    baseline = report['attribution']['critical_step']
    official = report['official_output']['critical_error']
    if official.get('unified_source') != 'llm_pick':
        return baseline
    chain = official.get('chain_min_unified_critical_step')
    if type(chain) is not int or not 0 <= chain < len(trace.messages):
        return baseline
    msg = trace.messages[chain]
    triggered = {t['step'] for t in report['triggers']}
    if msg.role == 'user' or msg.judgable is False or chain not in triggered:
        return baseline
    if policy == 'unsupported_pick' and baseline in triggered:
        return baseline
    return chain


def load_partition(cases, part, root, dataset):
    labels = list(map(json.loads, (dataset/'labels'/f'{part}.jsonl').read_text(encoding='utf-8').splitlines()))
    lookup = {r['input_sha256']: r for r in labels}
    if len(lookup) != len(cases) or len(lookup) != len(labels):
        raise ValueError(f'Label count mismatch: {part}')
    result = []
    for row, trace in cases:
        key = row['input_sha256']
        label = lookup[key]
        if (label['id'], label['dataset'], label['task_id']) != (row['id'], trace.dataset, trace.task_id):
            raise ValueError(f'Label mismatch: {key}')
        path = root/'reports'/part/f'{key}.json'
        base = {'id':row['id'], 'domain':row['domain'], 'group':row['group_id'], 'sha256':key,
                'gold':label['critical_error_step']}
        if not path.exists():
            if not (root/'failures'/part/f'{key}.json').is_file():
                raise ValueError(f'Unfinished: {part} {key}')
            result.append({**base, 'failed':True, 'baseline':None, **dict.fromkeys(POLICIES)})
            continue
        report = json.loads(path.read_bytes())
        if report['input_sha256'] != hashlib.sha256(trace.input_json().encode()).hexdigest():
            raise ValueError(f'Report mismatch: {key}')
        stage = stage_scores(report, base['gold'])
        result.append({**base, 'failed':False, 'baseline':stage['prediction_step'],
                       'trigger_recall':stage['trigger_recall'], 'candidate_retained':stage['candidate_retained'],
                       'usage':report['usage'], **{p:predict(p, report, trace) for p in POLICIES}})
    return result


def correct(row, policy):
    return row[policy] == row['gold']


def group_gain(rows, policy):
    by_group = defaultdict(list)
    for row in rows:
        by_group[row['group']].append(correct(row, policy) - correct(row, 'baseline'))
    return mean(mean(d) for d in by_group.values())


def paired(rows, policy):
    baseline = [score_prediction(r['baseline'], r['gold']) for r in rows]
    revised = [score_prediction(r[policy], r['gold']) for r in rows]
    gained = sum(b['exact_match'] and not a['exact_match'] for a,b in zip(baseline,revised))
    lost = sum(a['exact_match'] and not b['exact_match'] for a,b in zip(baseline,revised))
    discordant = gained + lost
    p = min(1., 2*sum(math.comb(discordant,k) for k in range(min(gained,lost)+1))/2**discordant)
    groups = defaultdict(list)
    for r in rows:
        groups[r['group']].append(int(correct(r,policy)) - int(correct(r,'baseline')))
    clusters = [(sum(v),len(v)) for v in groups.values()]
    rng = random.Random(3942)
    resamples = []
    for _ in range(5000):
        draws = rng.choices(clusters,k=len(clusters))
        resamples.append(sum(x for x,n in draws)/sum(n for x,n in draws))
    resamples.sort()
    metrics = ('exact_match','within_1','within_2')
    return {'n':len(rows),'groups':len(groups),'failed':sum(r['failed'] for r in rows),
            'baseline':{k:sum(a[k] for a in baseline) for k in metrics},
            'candidate':{k:sum(a[k] for a in revised) for k in metrics},
            'no_attribution':[sum(r[k] is None for r in rows) for k in ('baseline',policy)],
            'gained':gained,'lost':lost,'delta_pp':100*(gained-lost)/len(rows),'exact_mcnemar_p':p,
            'cluster_bootstrap_ci95_pp':[round(100*resamples[i],2) for i in (124,4874)],
            'trigger_hits':sum(r.get('trigger_recall',False) for r in rows),
            'candidate_pool_hits':sum(r.get('candidate_retained',False) for r in rows)}


def freeze(path, evidence):
    if path.exists() and json.loads(path.read_text(encoding='utf-8')) != evidence:
        raise ValueError(f'Frozen evidence changed: {path}')
    write(path, evidence)


def evaluate(dataset, root):
    groups = {p:[] for p in PARTS}
    for row, trace in inputs(dataset):
        groups[row['split']].append((row,trace))
    meta = json.loads((dataset/'dataset.json').read_text(encoding='utf-8'))
    for name, digest in meta['sha256'].items():
        if hashlib.sha256((dataset/name).read_bytes()).hexdigest() != digest:
            raise ValueError(name)
    def load(p):
        return load_partition(groups[p],p,root,dataset)
    train = load('train')
    gains = {p:group_gain(train,p) for p in POLICIES}
    selected = max(POLICIES,key=gains.get)
    frozen = {'policy':selected,'train_group_gains':gains,
              'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'protocol_sha256':hashlib.sha256((root/'protocol.md').read_bytes()).hexdigest(),
              'baseline_sha256':hashlib.sha256((root/'baseline.json').read_bytes()).hexdigest()}
    freeze(root/'candidate.json', frozen)
    dev = load('dev')
    validation = paired(dev,selected)
    passed = gains[selected] > 0 and validation['gained'] > validation['lost']
    freeze(root/'dev-decision.json', {'passed':passed,'selected':selected,'dev':validation})
    scores, usage = {}, Counter()
    domains = {}
    results = {'train':train,'dev':dev,'heldout':load('heldout'),'external_eval':load('external_eval')}
    for part, rows in results.items():
        scores[part] = paired(rows,selected)
        domains[part] = {d:paired([r for r in rows if r['domain']==d],selected) for d in sorted({r['domain'] for r in rows})}
        for row in rows:
            usage.update(row.get('usage',{}))
    hold, ext = scores['heldout'], scores['external_eval']
    promote = passed and hold['gained'] > hold['lost'] and hold['exact_mcnemar_p'] < .05 and ext['gained'] >= ext['lost']
    output = {'policy':selected,'dev_passed':passed,'promote':promote,'splits':scores,'domains':domains,
              'completed_report_usage':dict(usage),'candidate_additional_model_requests':0}
    write(root/'comparison.json',output)
    with (root/'paired-results.jsonl').open('w', encoding='utf-8') as stream:
        for part, rows in results.items():
            for r in rows:
                stream.write(json.dumps({'split':part, **{k:r[k] for k in ('id','group','domain','sha256','gold','baseline', selected,'failed')}},ensure_ascii=False)+'\n')
    lines = ['# 第一轮 RSI 配对评估', '', f"模型为 {json.loads((root/'baseline.json').read_text(encoding='utf-8'))['settings']['model']}，冻结策略为 `{selected}`。",
             '', '| 分区 | 条数 | 基线命中 | 候选命中 | 变化（百分点） | 改对 / 改错 |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for part, value in scores.items():
        n = value['n']
        old = value['baseline']['exact_match']
        new = value['candidate']['exact_match']
        lines.append(f"| {part} | {n} | {old}（{old/n:.1%}） | {new}（{new/n:.1%}） | {value['delta_pp']:+.1f} | {value['gained']} / {value['lost']} |")
    lines.extend(['', '候选通过上线验收。' if promote else '本轮保留平台现有归因策略。',
                  '', '训练集按任务组等权选择候选，开发集用于筛选，同域留出与跨域集用于最终验收。',
                  f"同域留出双侧配对精确检验 p = {hold['exact_mcnemar_p']:.4f}，跨域 p = {ext['exact_mcnemar_p']:.4f}。",
                  '样本量较小，尤其是分域结果。比较只覆盖这批失败轨迹的关键步骤定位。',
                  '', '分域结果、用量和区间估计见 comparison.json，逐条对照见 paired-results.jsonl。'])
    (root/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path('data/rsi/v1-lite'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.dataset,args.output)
