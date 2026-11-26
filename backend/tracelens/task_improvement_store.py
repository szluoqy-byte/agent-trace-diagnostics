"""Persist task-round recommendation snapshots and queued generation."""

import json
import uuid

from .task_improvements import snapshot_for_rows


class TaskImprovementStore:
    def migrate_task_improvements(self):
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS task_improvements (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    status TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL,
                    snapshot TEXT NOT NULL, results TEXT NOT NULL DEFAULT '{}', error TEXT
                );
                CREATE INDEX IF NOT EXISTS task_improvement_reports ON task_improvements(run_id, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS task_improvement_active ON task_improvements(run_id)
                    WHERE status IN ('queued','running');
            ''')

    def task_improvement_snapshot(self, run_id, goal):
        with self.connect() as db:
            rows = db.execute('''SELECT x.trace_id,t.title,j.id job_id,j.status,j.mode,
                json_extract(t.data,'$.outcome') AS outcome,
                CASE WHEN j.status='completed' AND j.mode='live' AND json_extract(t.data,'$.outcome')='failed'
                     THEN t.data END AS data,
                CASE WHEN j.status='completed' AND j.mode='live' AND json_extract(t.data,'$.outcome')='failed'
                     THEN j.result END AS report, v.id AS review_id,v.data AS review
                FROM run_items x JOIN traces t ON t.id=x.trace_id
                LEFT JOIN jobs j ON j.id=(SELECT a.job_id FROM run_attempts a
                    WHERE a.run_id=x.run_id AND a.trace_id=x.trace_id ORDER BY a.attempt DESC LIMIT 1)
                LEFT JOIN reviews v ON v.rowid=(SELECT r.rowid FROM reviews r
                    WHERE r.job_id=j.id ORDER BY r.created_at DESC,r.rowid DESC LIMIT 1)
                WHERE x.run_id=? ORDER BY x.position''', (run_id,))
            return snapshot_for_rows(rows, goal)

    def enqueue_task_improvement(self, task_id, run_id, model, snapshot):
        from .store import now
        frozen = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('SELECT id FROM analysis_runs WHERE task_id=? ORDER BY number DESC LIMIT 1', (task_id,)).fetchone()
            if not latest or latest['id'] != run_id:
                raise ValueError('请在最新分析轮次生成任务改进建议。')
            prior = db.execute('''SELECT * FROM task_improvements WHERE run_id=?
                ORDER BY created_at DESC,rowid DESC LIMIT 1''', (run_id,)).fetchone()
            if prior and prior['status'] in {'queued', 'running'}:
                return {'id': prior['id'], 'status': prior['status']}
            status = 'queued' if snapshot['agents'] else 'insufficient'
            if prior and status == 'insufficient' and prior['status'] == status and prior['snapshot'] == frozen:
                return {'id': prior['id'], 'status': prior['status']}
            # A retry against identical evidence can reuse completed Agent groups.
            results = {}
            if prior and prior['status'] in {'failed', 'partial'} and prior['snapshot'] == frozen and prior['model'] == model:
                results = {key: result for key, result in json.loads(prior['results']).items() if result['status'] == 'completed'}
            report_id = uuid.uuid4().hex
            db.execute('''INSERT INTO task_improvements(id,run_id,status,model,created_at,snapshot,results)
                VALUES (?,?,?,?,?,?,?)''', (report_id, run_id, status, model, now(), frozen, json.dumps(results, ensure_ascii=False)))
            return {'id': report_id, 'status': status}

    def task_improvements(self, run_id, report_id=None):
        with self.connect() as db:
            db.execute('BEGIN')
            reports = [dict(row) for row in db.execute('''SELECT id,status,model,created_at FROM task_improvements
                WHERE run_id=? ORDER BY created_at DESC,rowid DESC''', (run_id,))]
            selected_id = report_id or (reports[0]['id'] if reports else None)
            row = db.execute('SELECT * FROM task_improvements WHERE run_id=? AND id=?', (run_id, selected_id)).fetchone()
            return {'reports': reports, 'report': self._improvement_record(row) if row else None}

    def claim_task_improvement(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT i.*,r.config FROM task_improvements i JOIN analysis_runs r ON r.id=i.run_id
                WHERE i.status='queued' ORDER BY i.created_at,i.rowid LIMIT 1''').fetchone()
            if not row:
                return None
            db.execute("UPDATE task_improvements SET status='running' WHERE id=?", (row['id'],))
            return self._improvement_record(row)

    def update_task_improvement(self, report_id, results, *, status='running', error=None):
        with self.connect() as db:
            db.execute("""UPDATE task_improvements SET status=?,results=?,error=?
                WHERE id=? AND status='running'""", (status, json.dumps(results, ensure_ascii=False), error, report_id))

    @staticmethod
    def _improvement_record(row):
        record = dict(row)
        for field in ('snapshot', 'results', 'config'):
            if field in record:
                record[field] = json.loads(record[field])
        return record
