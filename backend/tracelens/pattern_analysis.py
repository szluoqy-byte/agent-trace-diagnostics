"""Deterministic aggregation of saved critical errors and their evidence."""

from collections import Counter, defaultdict
from hashlib import sha256
import json
import re
import unicodedata

ALGORITHM_VERSION = 'critical-error-grouping-v4'
MAPPING_VERSION = '2'

# Match whole objects. Removing domain qualifiers or arbitrary IDs can merge distinct rules.
OBJECT_ALIASES = {
    'explicit user confirmation': '明确的用户确认',
    '用户明确确认': '明确的用户确认',
    '明确的用户确认': '明确的用户确认',
}
CATEGORY_ALIASES = {
    'act.unsafeorforbiddenaction': 'act.UnsafeOrForbiddenAction',
    'env.toolexecutionfailure': 'env.ToolExecutionFailure',
}


# Mask incidental references while retaining policy thresholds, tool names, and code symbols.
_REFERENCES = (
    (re.compile(r'\b(?:at|from|in|see)\s+step\s*#?\s*\d+|(?:见|来自)\s*(?:步骤|第)\s*#?\s*\d+\s*步?', re.I), '<step>'),
    (re.compile(r'\b((?i:reservation|booking))(?:\s+id)?\s*[:#]?\s+((?=[A-Z0-9]*\d)[A-Z0-9]{5,10})\b'), r'\1 <id>'),
    (re.compile(r'\b(order|product|variant|item)\s*(?:id\s*[:#]?\s*|#\s*)((?=[A-Z0-9-]*\d)[A-Z0-9-]{5,})\b', re.I), r'\1 <id>'),
)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()) if isinstance(value, str) else ''


def object_key(value: str) -> str:
    for pattern, replacement in _REFERENCES:
        value = pattern.sub(replacement, value)
    return value.casefold()


def project_trace(trace, job, report, review):
    item = {**trace, **(job or {}), 'review_id': review.get('id'), 'review_decision': review.get('decision'),
            'engine_version': report.get('engine_version'), 'model': report.get('model') or (job or {}).get('model'),
            'state': 'included'}
    attribution = report.get('attribution') or {}
    step = attribution.get('critical_step')
    category = normalized(attribution.get('category'))
    instance = report.get('instance') or {}
    obj = normalized(instance.get('object'))
    decision = review.get('decision')
    if trace['kind'] == 'demo' or job and job['mode'] == 'demo':
        item['state'] = 'demo'
    elif trace['outcome'] != 'failed':
        item['state'] = 'succeeded' if trace['outcome'] == 'succeeded' else 'unknown'
    elif not job or job['status'] in {'queued', 'running'}:
        item['state'] = 'pending'
    elif job['status'] != 'completed':
        item['state'] = 'diagnosis_failed'
    elif report.get('schema_version') != 2:
        item['state'] = 'unsupported'
    elif decision == 'rejected':
        item['state'] = 'rejected'
    elif decision in {'modified', 'confirmed'} and (review.get('critical_step') != step or
            normalized(review.get('category')).casefold() != category.casefold()):
        item.update(state='review_needs_evidence', critical_step=review.get('critical_step'),
                    category=review.get('category'))
    elif type(step) is not int or not 0 <= step < trace['steps']:
        item['state'] = 'unattributed'
    elif not obj:
        item.update(state='missing_instance', critical_step=step, category=category if category != '未提供分类' else None,
                    attribution_source=attribution.get('source'))
    elif not category or category == '未提供分类':
        item.update(state='unattributed', critical_step=step, attribution_source=attribution.get('source'))
    else:
        evidence = report.get('evidence') or []
        evidence = [e for e in evidence if e.get('matched') is True and e.get('quote') and
                    type(e.get('step')) is int and 0 <= e['step'] < trace['steps'] and
                    (e.get('source_kind') == 'original' and e.get('original_matched') is True or
                     e.get('source_kind') == 'compressed' and e.get('view_matches'))]
        if not evidence:
            item['state'] = 'unlocated'
        else:
            anchor = min(evidence, key=lambda e: (e['role'] != 'commitment', e['step'] != step,
                         e['source_kind'] != 'original', abs(e['step'] - step), e['step']))
            tool = report.get('tool')
            if tool in {'assistant', 'tool', 'system', 'user'}:
                tool = None
            item.update(critical_step=step, instance_id=instance.get('id'), attribution_source=attribution.get('source'),
                        category=CATEGORY_ALIASES.get(category.casefold(), category), object=OBJECT_ALIASES.get(obj.casefold(), obj),
                        responsibility=instance.get('attribution') or 'unknown', lifecycle=instance.get('error_state'),
                        tool=tool, evidence={k: v for k, v in anchor.items() if k != 'role'},
                        evidence_role=anchor['role'], instance_source=report.get('instance_source'))
    return item


def aggregate(items):
    """Support is the number of distinct eligible trajectories with the pattern."""
    counts = Counter(item['state'] for item in items)
    groups = defaultdict(list)
    denominator = counts['included']
    for item in items:
        if item['state'] == 'included':
            key = (item['responsibility'], item['category'].casefold(), object_key(item['object']))
            groups[key].append(item)

    patterns = [_group(key, rows, denominator) for key, rows in groups.items()]
    patterns.sort(key=lambda pattern: (-pattern['count'], pattern['id']))
    return {'counts': dict(counts), 'selected': len(items), 'eligible': denominator,
            'business_failed': sum(item['outcome'] == 'failed' for item in items),
            'business_known': sum(item['outcome'] in {'failed', 'succeeded'} for item in items),
            'repeated': sum(pattern['count'] >= 2 for pattern in patterns), 'patterns': patterns}


def _group(key, rows, denominator):
    ordered = sorted(rows, key=lambda row: (row['review_decision'] not in {'confirmed', 'modified'},
                     row['evidence']['source_kind'] != 'original', row['trace_id']))
    representative = ordered[0]
    ids = [row['trace_id'] for row in ordered]
    return {'id': digest(key)[:24], 'category': representative['category'], 'object': representative['object'],
            'responsibility': representative['responsibility'], 'count': len(ids), 'denominator': denominator,
            'share': len(ids) / denominator, 'trace_ids': ids, 'representatives': ids[:3],
            'examples': [{'title': row['title'], 'evidence_kind': row['evidence']['source_kind']}
                         for row in ordered[:3]]}
