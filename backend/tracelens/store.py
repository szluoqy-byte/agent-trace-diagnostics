import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .models import Trajectory
from .deletion import DeletionStore
from .model_connections import ModelConnections
from .task_store import TaskStore
from .pattern_store import PatternStore
from .trajdebug import attribution_fields


def now():
    return datetime.now(timezone.utc).isoformat()


class Store(TaskStore, PatternStore, DeletionStore):
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.model_connections = ModelConnections(directory)
        self.path = directory / "tracelens.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS traces (
                id TEXT PRIMARY KEY, hash TEXT UNIQUE, title TEXT, data TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, trace_id TEXT REFERENCES traces(id), mode TEXT,
                status TEXT, stage TEXT, progress INTEGER, attempts INTEGER DEFAULT 0,
                error TEXT, result TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), data TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS standalone_job_configs (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), config TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feedback_runs (
                id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), status TEXT,
                error TEXT, result TEXT, created_at TEXT, updated_at TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_feedback ON feedback_runs(job_id)
                WHERE status IN ('queued', 'running');
            """)
        self.migrate_tasks()
        self.migrate_patterns()
        self.migrate_attribution()

    def migrate_attribution(self):
        """Refresh the saved projection while retaining the upstream output and review snapshots."""
        key = 'canonical_attribution_v1'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM workspace_meta WHERE key=?', (key,)).fetchone():
                return
            rows = db.execute("""SELECT id,json_extract(result,'$.official_output.critical_error') AS source,
                json_extract(result,'$.attribution') AS projection,json_extract(result,'$.instances') AS instances
                FROM jobs WHERE status='completed' AND mode='live'
                  AND json_extract(result,'$.algorithm')='TrajDebug'
                  AND json_extract(result,'$.schema_version')=2
                  AND json_type(result,'$.official_output.critical_error')='object'
                  AND json_type(result,'$.attribution')='object'""").fetchall()
            for row in rows:
                projection = json.loads(row['projection'])
                fields = attribution_fields(json.loads(row['source']), json.loads(row['instances'] or '[]'),
                                            projection.get('critical_step'))
                if any(projection.get(key) != value for key, value in fields.items()):
                    projection.update(fields)
                    db.execute("UPDATE jobs SET result=json_set(result,'$.attribution',json(?)) WHERE id=?",
                               (json.dumps(projection,ensure_ascii=False),row['id']))
            db.execute('INSERT INTO workspace_meta VALUES (?,?)', (key,'1'))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def row(row):
        if not row:
            return None
        result = dict(row)
        for key in ("data", "result"):
            if key in result and result[key]:
                result[key] = json.loads(result[key])
        return result

    def add_trace(self, trace: Trajectory):
        with self.connect() as db:
            trace_id = self._insert_trace(db, trace)
            return self.row(db.execute("SELECT * FROM traces WHERE id=?", (trace_id,)).fetchone())

    def traces(self):
        with self.connect() as db:
            return [self.row(r) for r in db.execute("SELECT * FROM traces ORDER BY created_at DESC")]

    def trace(self, trace_id):
        with self.connect() as db:
            return self.row(db.execute("SELECT * FROM traces WHERE id=?", (trace_id,)).fetchone())

    def enqueue(self, trace_id, mode, *, config=None, retry_of=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM traces WHERE id=?", (trace_id,)).fetchone():
                raise ValueError("轨迹已删除，请刷新后重试")
            if retry_of is not None:
                self.assert_job_writable(retry_of, db)
            active = db.execute("SELECT * FROM jobs WHERE trace_id=? AND status IN ('queued','running') AND NOT EXISTS(SELECT 1 FROM run_attempts WHERE job_id=jobs.id)", (trace_id,)).fetchone()
            if active:
                return self.row(active)
            job_id = uuid.uuid4().hex
            db.execute("INSERT INTO jobs VALUES (?, ?, ?, 'queued', '等待调度', 0, 0, NULL, NULL, ?, ?)",
                       (job_id, trace_id, mode, now(), now()))
            if config is not None:
                db.execute("INSERT INTO standalone_job_configs VALUES (?, ?)", (job_id, json.dumps(config)))
        return self.job(job_id)

    def jobs(self):
        with self.connect() as db:
            return [self.row(r) for r in db.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    def job(self, job_id):
        with self.connect() as db:
            return self.row(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def update(self, job_id, **fields):
        allowed = {"status", "stage", "progress", "error", "result", "attempts"}
        if not fields.keys() <= allowed:
            raise ValueError("Invalid job field")
        if "result" in fields:
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        fields["updated_at"] = now()
        with self.connect() as db:
            db.execute("UPDATE jobs SET " + ", ".join(f"{key}=?" for key in fields) + " WHERE id=?", (*fields.values(), job_id))

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='failed', stage='运行中断', error='服务重启导致任务中断，可点击重试', updated_at=? WHERE status='running'", (now(),))
            db.execute("UPDATE feedback_runs SET status='failed', error='服务重启导致建议生成中断，可重试', updated_at=? WHERE status='running'", (now(),))

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='running', attempts=attempts+1, stage='轨迹解析', updated_at=? WHERE id=?", (now(), row["id"]))
                return self.row(row)
        return None

    def add_review(self, job_id, data):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assert_job_writable(job_id, db)
            db.execute("INSERT INTO reviews VALUES (?, ?, ?, ?)", (uuid.uuid4().hex, job_id, json.dumps(data, ensure_ascii=False), now()))

    def reviews(self, job_id):
        with self.connect() as db:
            return [self.row(r) for r in db.execute("SELECT * FROM reviews WHERE job_id=? ORDER BY created_at DESC,rowid DESC", (job_id,))]

    def feedback_history(self, job_id):
        with self.connect() as db:
            return [self.row(r) for r in db.execute("SELECT * FROM feedback_runs WHERE job_id=? ORDER BY created_at DESC, rowid DESC", (job_id,))]

    def latest_feedback(self, job_id):
        with self.connect() as db:
            return self.row(db.execute("SELECT * FROM feedback_runs WHERE job_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (job_id,)).fetchone())

    def enqueue_feedback(self, job_id, *, regenerate_from=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.assert_job_writable(job_id, db)
            latest = db.execute("SELECT * FROM feedback_runs WHERE job_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (job_id,)).fetchone()
            if regenerate_from is not None:
                if not latest or latest['id'] != regenerate_from or latest['status'] != 'completed':
                    raise ValueError('改进建议已更新，请刷新后重试')
            elif latest and latest['status'] in {'queued', 'running', 'completed'}:
                return self.row(latest)
            feedback_id, stamp = uuid.uuid4().hex, now()
            db.execute("INSERT INTO feedback_runs VALUES (?, ?, 'queued', NULL, NULL, ?, ?)", (feedback_id, job_id, stamp, stamp))
            return self.row(db.execute("SELECT * FROM feedback_runs WHERE id=?", (feedback_id,)).fetchone())

    def claim_feedback(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM feedback_runs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE feedback_runs SET status='running', updated_at=? WHERE id=?", (now(), row['id']))
                return self.row(row)
        return None

    def update_feedback(self, feedback_id, *, status, result=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE feedback_runs SET status=?, result=?, error=?, updated_at=? WHERE id=?",
                       (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, now(), feedback_id))
