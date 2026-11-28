"""Persistent aggregate analysis snapshots. Source diagnosis rows are read-only."""

import json
import uuid
from collections import Counter

from .pattern_analysis import ALGORITHM_VERSION, MAPPING_VERSION, aggregate, digest, normalized, project_trace


SCHEMA = """
CREATE TABLE IF NOT EXISTS pattern_runs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES analysis_tasks(id), number INTEGER NOT NULL,
 created_at TEXT NOT NULL, algorithm_version TEXT NOT NULL, mapping_version TEXT NOT NULL,
 snapshot_hash TEXT NOT NULL, scope TEXT NOT NULL, result TEXT NOT NULL,
 request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, UNIQUE(task_id,number));
CREATE TABLE IF NOT EXISTS pattern_run_inputs (
 run_id TEXT NOT NULL REFERENCES pattern_runs(id), trace_id TEXT NOT NULL REFERENCES traces(id),
 job_id TEXT REFERENCES jobs(id), review_id TEXT REFERENCES reviews(id), snapshot TEXT NOT NULL,
 PRIMARY KEY(run_id,trace_id));
CREATE TABLE IF NOT EXISTS pattern_reviews (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES pattern_runs(id), pattern_id TEXT NOT NULL,
 decision TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pattern_review_history ON pattern_reviews(run_id,pattern_id,created_at);
"""


def decode(row, *fields):
    result = dict(row)
    for field in fields:
        result[field] = json.loads(result[field])
    return result


class PatternStore:
    def migrate_patterns(self):
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            if not any(row['name'] == 'kind' for row in db.execute('PRAGMA table_info(analysis_tasks)')):
                db.execute("ALTER TABLE analysis_tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'diagnosis'")

    def pattern_preview(self, scope, overrides):
        run_id = scope.get('run_id')
        ids = scope.get('trace_ids')
        with self.connect() as db:
            db.execute('BEGIN')
            if run_id:
                run = db.execute("""SELECT r.id FROM analysis_runs r JOIN analysis_tasks t ON t.id=r.task_id
                    WHERE r.id=? AND r.task_id=? AND t.kind='diagnosis'""", (run_id, scope['task_id'])).fetchone()
                if not run:
                    raise LookupError('来源诊断轮次不存在')
                members = [row['trace_id'] for row in db.execute(
                    'SELECT trace_id FROM run_items WHERE run_id=? ORDER BY position', (run_id,))]
                if ids and not set(ids) <= set(members):
                    raise ValueError('所选轨迹不属于来源诊断轮次')
                ids = ids or members
            ids = sorted(set(ids))
            if not 1 <= len(ids) <= 500:
                raise ValueError('每次聚合需要选择 1–500 条轨迹')
            if not set(overrides) <= set(ids):
                raise ValueError('所选诊断包含范围外的轨迹')
            encoded = json.dumps(ids)
            traces = [dict(row) for row in db.execute("""SELECT id AS trace_id,title,
                json_extract(data,'$.outcome') AS outcome,json_extract(data,'$.kind') AS kind,
                json_array_length(data,'$.messages') AS steps
                FROM traces WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id""", (encoded,))]
            if len(traces) != len(ids):
                raise LookupError('所选轨迹不存在')

            choices = {trace_id: [] for trace_id in ids}
            jobs = db.execute("""SELECT j.trace_id,j.id AS job_id,j.status,j.mode,j.created_at,j.updated_at,
                t.id AS source_task_id,t.name AS source_task,r.id AS source_run_id,r.number AS source_run_number,
                json_extract(r.config,'$.model') AS model
                FROM jobs j JOIN run_attempts a ON a.job_id=j.id
                JOIN analysis_runs r ON r.id=a.run_id JOIN analysis_tasks t ON t.id=r.task_id
                WHERE j.trace_id IN (SELECT value FROM json_each(?)) AND (? IS NULL OR r.id=?)
                AND a.attempt=(SELECT MAX(attempt) FROM run_attempts WHERE run_id=a.run_id AND trace_id=a.trace_id)
                ORDER BY (j.status='completed' AND j.mode='live') DESC,j.created_at DESC,j.id""", (encoded, run_id, run_id))
            for job in jobs:
                choices[job['trace_id']].append(dict(job))
            selected = {}
            for trace_id, candidates in choices.items():
                job_id = overrides.get(trace_id)
                job = next((job for job in candidates if job['job_id'] == job_id and job['status'] == 'completed'), None)
                if job_id and job is None:
                    raise ValueError('所选诊断记录不可用于该轨迹')
                selected[trace_id] = job or (candidates[0] if candidates else None)

            reports = self._pattern_source_reports(db, [job['job_id'] for job in selected.values()
                                                         if job and job['status'] == 'completed'])
            items = []
            for trace in traces:
                job = selected[trace['trace_id']]
                report, review = reports.get(job['job_id'], ({}, {})) if job else ({}, {})
                items.append(project_trace(trace, job, report, review))

        fingerprint = digest([ALGORITHM_VERSION, MAPPING_VERSION, ids, items])
        return {'snapshot_hash': fingerprint, 'scope': {**scope, 'trace_ids': ids}, 'items': items,
                'choices': {trace_id: [job for job in rows if job['status'] == 'completed']
                            for trace_id, rows in choices.items() if rows},
                'counts': dict(Counter(item['state'] for item in items)), 'engine_versions': sorted(set(
                    (item['engine_version'] or '未知',item['model'] or '未知') for item in items if item['state']=='included'))}

    @staticmethod
    def _pattern_source_reports(db, ids):
        rows = db.execute("""SELECT j.id, json_extract(j.result,'$.schema_version') AS schema_version,
            json_extract(j.result,'$.engine_version') AS engine_version, json_extract(j.result,'$.model') AS model,
            json_extract(j.result,'$.attribution') AS attribution,
            json_extract(j.result,'$.comparison.state') AS comparison_state,
            json_extract(j.result,'$.instances') AS instances,
            json_extract(j.result,'$.triggers') AS triggers,
            CASE WHEN json_type(j.result,'$.attribution.critical_step')='integer'
                 AND json_extract(j.result,'$.attribution.critical_step')>=0
                 THEN json_extract(t.data, '$.messages[' || json_extract(j.result,'$.attribution.critical_step') || '].name')
            END AS tool,
            v.id AS review_id,v.data AS review
            FROM jobs j JOIN traces t ON t.id=j.trace_id
            LEFT JOIN reviews v ON v.rowid=(SELECT rowid FROM reviews WHERE job_id=j.id ORDER BY created_at DESC,rowid DESC LIMIT 1)
                AND json_extract(v.data,'$.decision') IS NOT 'reset'
            WHERE j.id IN (SELECT value FROM json_each(?))""", (json.dumps(ids),))
        reports = {}
        for row in rows:
            report = dict(row)
            for key, default in (('attribution', {}), ('instances', []), ('triggers', []), ('review', {})):
                value = report[key]
                report[key] = json.loads(value) if isinstance(value, str) else value or default
            triggers = report.pop('triggers')
            instance, reason = PatternStore._pattern_instance(report['attribution'], report.pop('instances'), triggers)
            report.update(instance=instance, instance_source=reason)
            trigger_ids = set(instance.get('trigger_ids') or [])
            evidence = [{**anchor, 'role': 'lifecycle'} for anchor in (instance.get('lifecycle_evidence') or [])]
            for trigger in triggers:
                if trigger.get('id') in trigger_ids:
                    evidence.extend({**trigger[role], 'role': role} for role in ('commitment', 'reference') if trigger.get(role))
            review = report.pop('review')
            review['id'] = report.pop('review_id')
            report['evidence'] = evidence
            reports[row['id']] = (report, review)
        return reports

    @staticmethod
    def _pattern_instance(attribution, instances, triggers):
        instance_id = attribution.get('critical_instance_id')
        if instance_id:
            return next((i for i in instances if i.get('id') == instance_id), {}), 'official'
        step = attribution.get('critical_step')
        category = normalized(attribution.get('category')).casefold()
        if type(step) is not int or not category:
            return {}, None
        matching = {t['id']: t.get('attribution') for t in triggers
                    if t.get('id') and t.get('step') == step and normalized(t.get('taxonomy_tag')).casefold() == category}
        candidates = [i for i in instances if i.get('terminal_relevant') and not i.get('parse_failed')
                      and any(trigger in matching and matching[trigger] == i.get('attribution')
                              for trigger in i.get('trigger_ids', []))]
        return (candidates[0], 'unique_same_step') if len(candidates) == 1 else ({}, None)

    @staticmethod
    def _pattern_replay(db, request_id, request_hash):
        row = db.execute('SELECT id,task_id,request_hash FROM pattern_runs WHERE request_id=?', (request_id,)).fetchone()
        if row and row['request_hash'] != request_hash:
            raise ValueError('此提交标识已用于不同的请求')
        return {'task_id': row['task_id'], 'run_id': row['id']} if row else None

    def pattern_replay(self, request_id, request_hash):
        with self.connect() as db:
            return self._pattern_replay(db, request_id, request_hash)

    def save_pattern_run(self, name, task_id, preview, request_id, request_hash):
        from .store import now

        result = aggregate(preview['items'])
        run_id, stamp = uuid.uuid4().hex, now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            replay = self._pattern_replay(db, request_id, request_hash)
            if replay:
                return replay
            scope = preview['scope']
            if scope.get('run_id') and not db.execute('SELECT 1 FROM analysis_runs WHERE id=? AND task_id=?',
                                                      (scope['run_id'], scope['task_id'])).fetchone():
                raise ValueError('来源诊断已删除，请重新选择轨迹')
            if not task_id:
                task_id = uuid.uuid4().hex
                db.execute("""INSERT INTO analysis_tasks (id,name,agent,version,goal,created_at,kind)
                    VALUES (?,?,'','','',?,'failure_patterns')""", (task_id, name, stamp))
                db.executemany('INSERT INTO task_traces VALUES (?,?,?)',
                               [(task_id, item['trace_id'], i) for i, item in enumerate(preview['items'])])
            number = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM pattern_runs WHERE task_id=?', (task_id,)).fetchone()[0]
            db.execute('INSERT INTO pattern_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (run_id,task_id,number,stamp,ALGORITHM_VERSION,MAPPING_VERSION,preview['snapshot_hash'],
                        json.dumps(preview['scope']),json.dumps(result, ensure_ascii=False),request_id,request_hash))
            db.executemany('INSERT INTO pattern_run_inputs VALUES (?,?,?,?,?)',
                           [(run_id,item['trace_id'],item.get('job_id'),item.get('review_id'),json.dumps(item,ensure_ascii=False))
                            for item in preview['items']])
        return {'task_id': task_id, 'run_id': run_id}

    def pattern_runs(self, task_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT id,number,created_at,
                json_extract(result,'$.selected') AS selected,json_extract(result,'$.eligible') AS eligible,
                json_extract(result,'$.repeated') AS repeated
                FROM pattern_runs WHERE task_id=? ORDER BY number DESC""", (task_id,))]

    def pattern_run(self, task_id, run_id):
        with self.connect() as db:
            row = db.execute("""SELECT id,task_id,number,created_at,algorithm_version,mapping_version,snapshot_hash,scope,
                json_remove(result,'$.topics') AS result
                FROM pattern_runs WHERE task_id=? AND id=?""", (task_id,run_id)).fetchone()
            return decode(row,'scope','result') if row else None

    def pattern_items(self, run_id):
        with self.connect() as db:
            return [json.loads(row['snapshot']) for row in db.execute(
                "SELECT json_remove(snapshot,'$.topic') AS snapshot FROM pattern_run_inputs WHERE run_id=? ORDER BY trace_id", (run_id,))]

    def pattern_trajectories(self, trace_ids):
        with self.connect() as db:
            return {row['id']: json.loads(row['data']) for row in db.execute(
                'SELECT id,data FROM traces WHERE id IN (SELECT value FROM json_each(?))', (json.dumps(trace_ids),))}

    def pattern_reviews(self, run_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT v.pattern_id,v.decision,v.notes,v.created_at
                FROM pattern_reviews v JOIN pattern_runs r ON r.id=v.run_id
                WHERE v.run_id=? AND v.pattern_id IN (
                    SELECT json_extract(value,'$.id') FROM json_each(r.result,'$.patterns'))
                ORDER BY v.created_at DESC,v.rowid DESC""", (run_id,))]

    def add_pattern_review(self, run, pattern_id, decision, notes):
        from .store import now

        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('SELECT id FROM pattern_runs WHERE task_id=? ORDER BY number DESC LIMIT 1',
                                (run['task_id'],)).fetchone()
            if not latest or latest['id'] != run['id']:
                raise ValueError('历史聚合结果只读，请切换到最新分析')
            db.execute('INSERT INTO pattern_reviews VALUES (?,?,?,?,?,?)',
                       (uuid.uuid4().hex,run['id'],pattern_id,decision,notes,now()))
