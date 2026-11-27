"""Persistent task scopes and analysis rounds; diagnosis artifacts stay in jobs."""
import json
import uuid
from .diagnosis import DEFAULT_MODE
from .diagnosis.review_policy import review_status
from .diagnosis.synthesis import participating_attributions, resolve_synthesis
from .models import Trajectory
from .normalize import fingerprint


SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS analysis_tasks (
 id TEXT PRIMARY KEY, name TEXT, agent TEXT, version TEXT, goal TEXT, prompt_language TEXT NOT NULL DEFAULT 'en', created_at TEXT);
CREATE TABLE IF NOT EXISTS task_traces (
 task_id TEXT REFERENCES analysis_tasks(id), trace_id TEXT REFERENCES traces(id), position INTEGER,
 PRIMARY KEY(task_id,trace_id));
CREATE TABLE IF NOT EXISTS analysis_runs (
 id TEXT PRIMARY KEY, task_id TEXT REFERENCES analysis_tasks(id), number INTEGER,
 config TEXT, auto_feedback INTEGER, created_at TEXT, UNIQUE(task_id,number));
CREATE TABLE IF NOT EXISTS run_items (
 run_id TEXT REFERENCES analysis_runs(id), trace_id TEXT REFERENCES traces(id), position INTEGER,
 PRIMARY KEY(run_id,trace_id));
CREATE TABLE IF NOT EXISTS run_attempts (
 run_id TEXT, trace_id TEXT, job_id TEXT UNIQUE REFERENCES jobs(id), attempt INTEGER,
 fresh_cache INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(run_id,trace_id,attempt),
 FOREIGN KEY(run_id,trace_id) REFERENCES run_items(run_id,trace_id));
CREATE TABLE IF NOT EXISTS import_previews (id TEXT PRIMARY KEY, data TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS task_run_order ON analysis_runs(task_id,number DESC);
CREATE INDEX IF NOT EXISTS feedback_job_order ON feedback_runs(job_id,created_at DESC);
CREATE INDEX IF NOT EXISTS review_job_order ON reviews(job_id,created_at DESC);
CREATE INDEX IF NOT EXISTS task_membership_by_trace ON task_traces(trace_id,task_id);
CREATE INDEX IF NOT EXISTS jobs_by_trace_status ON jobs(trace_id,status);
DROP INDEX IF EXISTS one_active_job;
"""


class TaskStore:
    def migrate_tasks(self):
        from .store import now
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            task_columns = {row['name'] for row in db.execute('PRAGMA table_info(analysis_tasks)')}
            if 'draft_auto_feedback' not in task_columns:
                db.execute('ALTER TABLE analysis_tasks ADD COLUMN draft_auto_feedback INTEGER NOT NULL DEFAULT 0')
            if 'prompt_language' not in task_columns:
                db.execute("ALTER TABLE analysis_tasks ADD COLUMN prompt_language TEXT NOT NULL DEFAULT 'en'")
            if 'diagnosis_mode' not in task_columns:
                db.execute("ALTER TABLE analysis_tasks ADD COLUMN diagnosis_mode TEXT NOT NULL DEFAULT 'trajdebug'")
            if 'model_connection_id' not in task_columns:
                db.execute('ALTER TABLE analysis_tasks ADD COLUMN model_connection_id TEXT')
            if not any(row['name'] == 'fresh_cache' for row in db.execute('PRAGMA table_info(run_attempts)')):
                db.execute('ALTER TABLE run_attempts ADD COLUMN fresh_cache INTEGER NOT NULL DEFAULT 0')
            if db.execute("SELECT 1 FROM workspace_meta WHERE key='task_migration_v1'").fetchone():
                return
            traces = db.execute('SELECT id FROM traces ORDER BY created_at,id').fetchall()
            if traces:
                task_id, stamp = uuid.uuid4().hex, now()
                db.execute('INSERT INTO analysis_tasks (id,name,agent,version,goal,prompt_language,created_at) VALUES (?,?,?,?,?,?,?)',
                           (task_id, '历史轨迹 · 已有数据', '', '', '重构前导入的轨迹与分析结果，原始记录完整保留。', 'en', stamp))
                for i, trace in enumerate(traces):
                    db.execute('INSERT INTO task_traces VALUES (?,?,?)', (task_id, trace['id'], i))
                groups = {t['id']: db.execute('SELECT id,result,created_at FROM jobs WHERE trace_id=? ORDER BY created_at,rowid', (t['id'],)).fetchall() for t in traces}
                count = max((len(g) for g in groups.values()), default=0)
                for index in range(count):
                    run_id = uuid.uuid4().hex
                    attached = [g[index-(count-len(g))] for g in groups.values() if index >= count-len(g)]
                    models = sorted({(json.loads(j['result'] or '{}').get('model') or '') for j in attached} - {''})
                    config = {'legacy': True, 'model': ' / '.join(models) or '历史配置', 'note': '迁移已有诊断；缺失的执行配置不作推断。'}
                    db.execute('INSERT INTO analysis_runs VALUES (?,?,?,?,?,?)',
                               (run_id, task_id, index+1, json.dumps(config, ensure_ascii=False), 0, min(j['created_at'] for j in attached)))
                    for i, trace in enumerate(traces):
                        db.execute('INSERT INTO run_items VALUES (?,?,?)', (run_id, trace['id'], i))
                        group = groups[trace['id']]
                        offset = index-(count-len(group))
                        if offset >= 0:
                            db.execute('INSERT INTO run_attempts (run_id,trace_id,job_id,attempt) VALUES (?,?,?,1)',
                                       (run_id, trace['id'], group[offset]['id']))
            db.execute("INSERT INTO workspace_meta VALUES ('task_migration_v1', '1')")

    def task(self, task_id):
        with self.connect() as db:
            return self.row(db.execute('SELECT * FROM analysis_tasks WHERE id=?', (task_id,)).fetchone())

    def freeze_legacy_runs(self, config):
        if not config.get('base_url') or not config.get('model'):
            return
        with self.connect() as db:
            for row in db.execute('SELECT id,config FROM analysis_runs').fetchall():
                old = json.loads(row['config'])
                if old.get('legacy') and (not old.get('base_url') or not old.get('model')):
                    frozen = {**old, **config, 'legacy_model':old.get('model'),
                              'note':'迁移前的配置未完整保留；已有报告以原始记录为准，未运行项使用迁移时保存的系统配置。'}
                    db.execute('UPDATE analysis_runs SET config=? WHERE id=?', (json.dumps(frozen,ensure_ascii=False),row['id']))

    def tasks(self, kind='diagnosis'):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM analysis_tasks WHERE ?='all' OR kind=? ORDER BY created_at DESC,id", (kind,kind))]

    def runs(self, task_id, *, latest_only=False):
        with self.connect() as db:
            result = [dict(row) for row in db.execute('''SELECT r.*,
                EXISTS (SELECT 1 FROM task_improvements i WHERE i.run_id=r.id AND i.status IN ('queued','running'))
                    AS improvement_active FROM analysis_runs r WHERE r.task_id=? ORDER BY r.number DESC LIMIT ?''',
                    (task_id, 1 if latest_only else -1))]
        for row in result:
            row['config'] = json.loads(row['config'])
            row['auto_feedback'] = bool(row['auto_feedback'])
            row['improvement_active'] = bool(row['improvement_active'])
        return result

    def task_inputs(self, task_id):
        with self.connect() as db:
            return [self.row(row) for row in db.execute('SELECT t.* FROM traces t JOIN task_traces x ON x.trace_id=t.id WHERE x.task_id=? ORDER BY x.position', (task_id,))]

    def trace_library(self, page, page_size, search, diagnosis, business, input_format, *, selection=False):
        # Keep a completed diagnosis visible even when the same trace is being reanalyzed.
        query = """
        WITH library AS (
            SELECT t.id,t.title,t.created_at,
                json_extract(t.data,'$.dataset') AS dataset,
                json_extract(t.data,'$.source') AS source,
                json_extract(t.data,'$.task_id') AS source_task_id,
                json_extract(t.data,'$.outcome') AS outcome,
                json_extract(t.data,'$.agent_identity.name') AS agent_name,
                json_array_length(t.data,'$.messages') AS steps,
                COALESCE(json_extract(t.data,'$.input_format'),
                         CASE WHEN json_type(t.data,'$.otel')='object' THEN 'otel_genai' ELSE 'messages' END) AS input_format,
                COALESCE((SELECT status FROM jobs WHERE trace_id=t.id AND status IN ('completed','running','queued','failed')
                    ORDER BY CASE status WHEN 'completed' THEN 0 WHEN 'running' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END LIMIT 1),
                    'pending') AS diagnosis
            FROM traces t
        )
        """
        conditions, args = [], []
        if search:
            conditions.append('(' + ' OR '.join(f'instr(lower({field}), lower(?))>0'
                              for field in ('title', 'id', 'source', 'dataset', 'source_task_id', 'agent_name')) + ')')
            args.extend([search] * 6)
        for field, value in (('diagnosis', diagnosis), ('outcome', business), ('input_format', input_format)):
            if value != 'all':
                conditions.append(f'{field}=?')
                args.append(value)
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self.connect() as db:
            if selection:
                items = db.execute(query + 'SELECT id,title,outcome,input_format FROM library' + where
                                   + ' ORDER BY created_at DESC,id', args)
                return {'items': [dict(row) for row in items]}
            db.execute('BEGIN')
            total = db.execute(query + 'SELECT COUNT(*) FROM library' + where, args).fetchone()[0]
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            items = db.execute(query + '''SELECT library.*,
                (SELECT COUNT(*) FROM task_traces WHERE trace_id=library.id) AS task_count
                FROM library''' + where + ' ORDER BY created_at DESC,id LIMIT ? OFFSET ?',
                [*args, page_size, (page - 1) * page_size]).fetchall()
        return {'items': [dict(row) for row in items], 'total': total, 'page': page, 'page_size': page_size, 'pages': pages}

    def trace_library_history(self, trace_id, *, latest_only=False):
        with self.connect() as db:
            db.execute('BEGIN')
            if not db.execute('SELECT 1 FROM traces WHERE id=?', (trace_id,)).fetchone():
                return None
            tasks = db.execute('''SELECT t.id,t.name,t.agent,t.version,t.kind FROM task_traces x
                JOIN analysis_tasks t ON t.id=x.task_id WHERE x.trace_id=? ORDER BY t.created_at DESC,t.id''', (trace_id,))
            diagnoses = db.execute('''SELECT j.id AS job_id,r.task_id,a.run_id,r.number,j.status,j.created_at,
                COALESCE(json_extract(r.config,'$.model'),json_extract(j.result,'$.model')) AS model
                FROM jobs j LEFT JOIN run_attempts a ON a.job_id=j.id
                LEFT JOIN analysis_runs r ON r.id=a.run_id
                WHERE j.trace_id=? AND (?=0 OR r.id IS NULL OR r.number=(SELECT MAX(number) FROM analysis_runs WHERE task_id=r.task_id))
                AND (a.job_id IS NULL OR a.attempt=(SELECT MAX(attempt) FROM run_attempts
                    WHERE run_id=a.run_id AND trace_id=a.trace_id)) ORDER BY j.created_at DESC,j.id''', (trace_id, latest_only))
            return {'tasks': [dict(row) for row in tasks], 'diagnoses': [dict(row) for row in diagnoses]}

    @staticmethod
    def _assert_latest(db, run_id):
        run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
        if not run or db.execute('SELECT MAX(number) FROM analysis_runs WHERE task_id=?', (run['task_id'],)).fetchone()[0] != run['number']:
            raise ValueError('历史分析记录只读，请切换到最新分析')

    def assert_job_writable(self, job_id, db=None):
        if db is None:
            with self.connect() as connection:
                return self.assert_job_writable(job_id, connection)
        if not db.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone():
            raise ValueError('诊断记录已删除，请刷新后重试')
        mapping = db.execute('SELECT * FROM run_attempts WHERE job_id=?', (job_id,)).fetchone()
        if mapping:
            self._assert_latest(db, mapping['run_id'])
            newest = db.execute('SELECT MAX(attempt) FROM run_attempts WHERE run_id=? AND trace_id=?', (mapping['run_id'], mapping['trace_id'])).fetchone()[0]
            if mapping['attempt'] != newest:
                raise ValueError('这是已保留的历史请求，请操作当前最新请求')

    def job_config(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT r.config FROM analysis_runs r JOIN run_attempts a ON a.run_id=r.id WHERE a.job_id=?', (job_id,)).fetchone()
            if row is None:
                row = db.execute('SELECT config FROM standalone_job_configs WHERE job_id=?', (job_id,)).fetchone()
            return json.loads(row['config']) if row else None

    def runtime_settings(self, defaults, fields):
        """Read persisted workspace runtime settings with safe defaults."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM workspace_meta WHERE key='runtime_settings_v1'").fetchone()
        stored = {}
        if row:
            try:
                value = json.loads(row['value'])
                if isinstance(value, dict):
                    stored = value
            except (TypeError, ValueError):
                pass
        return {key: stored.get(key, getattr(defaults, key)) for key in fields}

    def save_runtime_settings(self, values):
        with self.connect() as db:
            db.execute("INSERT INTO workspace_meta(key,value) VALUES('runtime_settings_v1', ?)"
                       " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (json.dumps(values, ensure_ascii=False),))

    def save_preview(self, data):
        from .store import now
        token = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("DELETE FROM import_previews WHERE julianday(created_at)<julianday('now','-1 day')")
            db.execute('INSERT INTO import_previews VALUES (?,?,?)', (token, json.dumps(data, ensure_ascii=False), now()))
        return token

    def preview(self, token):
        with self.connect() as db:
            row = db.execute("SELECT data FROM import_previews WHERE id=? AND julianday(created_at)>=julianday('now','-1 day')", (token,)).fetchone()
            return json.loads(row['data']) if row else None

    @staticmethod
    def _insert_trace(db, trace):
        from .store import now
        digest = fingerprint(trace)
        inserted = db.execute('INSERT OR IGNORE INTO traces VALUES (?,?,?,?,?)',
                              (uuid.uuid4().hex, digest, trace.title, trace.model_dump_json(), now()))
        if not inserted.rowcount and trace.deepagent is not None:
            db.execute("""UPDATE traces SET data=json_set(data,'$.deepagent',json(?),'$.agent_identity',json(?))
                       WHERE hash=? AND json_extract(data,'$.deepagent') IS NULL""",
                       (trace.deepagent.model_dump_json(), trace.agent_identity.model_dump_json() if trace.agent_identity else 'null', digest))
        return db.execute('SELECT id FROM traces WHERE hash=?', (digest,)).fetchone()['id']

    def create_task(self, fields, *, traces=None, trace_ids=None, config=None, auto_feedback=False):
        from .store import now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ids = list(dict.fromkeys(trace_ids if trace_ids is not None else [self._insert_trace(db, t) for t in traces]))
            if trace_ids is not None and len(ids) != db.execute(
                'SELECT COUNT(*) FROM traces WHERE id IN (SELECT value FROM json_each(?))', (json.dumps(ids),)
            ).fetchone()[0]:
                raise ValueError('所选轨迹已删除，请刷新后重试')
            task_id = uuid.uuid4().hex
            db.execute('''INSERT INTO analysis_tasks
                (id,name,agent,version,goal,prompt_language,created_at,draft_auto_feedback,diagnosis_mode,model_connection_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (task_id, *(fields[k] for k in ('name','agent','version','goal','prompt_language')),
                 now(), int(auto_feedback), fields.get('diagnosis_mode', DEFAULT_MODE), fields.get('model_connection_id')))
            for i, trace_id in enumerate(ids):
                db.execute('INSERT INTO task_traces VALUES (?,?,?)', (task_id, trace_id, i))
            run_id = self._start_run(db, task_id, config, auto_feedback) if config is not None else None
        return task_id, run_id

    def start_run(self, task_id, config, auto_feedback, *, model_connection_id=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run_id = self._start_run(db, task_id, config, auto_feedback)
            if model_connection_id is not None:
                db.execute('UPDATE analysis_tasks SET model_connection_id=? WHERE id=?', (model_connection_id, task_id))
            return run_id

    def _start_run(self, db, task_id, config, auto_feedback):
        from .store import now
        if not db.execute('SELECT 1 FROM analysis_tasks WHERE id=?', (task_id,)).fetchone():
            raise ValueError('分析任务已删除，请刷新后重试')
        active = db.execute("""SELECT 1 FROM run_attempts a JOIN analysis_runs r ON r.id=a.run_id
          JOIN jobs j ON j.id=a.job_id LEFT JOIN feedback_runs f ON f.job_id=j.id
          WHERE r.task_id=? AND (j.status IN ('queued','running') OR f.status IN ('queued','running'))
          UNION ALL SELECT 1 FROM task_improvements i JOIN analysis_runs r ON r.id=i.run_id
          WHERE r.task_id=? AND i.status IN ('queued','running') LIMIT 1""", (task_id, task_id)).fetchone()
        if active:
            raise ValueError('当前任务仍在处理，请完成后再重新分析')
        number = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM analysis_runs WHERE task_id=?', (task_id,)).fetchone()[0]
        run_id = uuid.uuid4().hex
        config = {**config, 'latest_only': True}
        if 'diagnosis_mode' in config:
            db.execute('UPDATE analysis_tasks SET diagnosis_mode=? WHERE id=?', (config['diagnosis_mode'], task_id))
        db.execute('INSERT INTO analysis_runs VALUES (?,?,?,?,?,?)', (run_id, task_id, number, json.dumps(config, ensure_ascii=False), int(auto_feedback), now()))
        for row in db.execute('SELECT * FROM task_traces WHERE task_id=? ORDER BY position', (task_id,)).fetchall():
            db.execute('INSERT INTO run_items VALUES (?,?,?)', (run_id, row['trace_id'], row['position']))
            self._enqueue_item(db, run_id, row['trace_id'])
        return run_id

    @staticmethod
    def _enqueue_item(db, run_id, trace_id, *, regenerate=False):
        from .store import now
        latest = db.execute('SELECT j.status,a.attempt,a.fresh_cache FROM run_attempts a JOIN jobs j ON j.id=a.job_id WHERE a.run_id=? AND a.trace_id=? ORDER BY a.attempt DESC LIMIT 1', (run_id, trace_id)).fetchone()
        if latest and latest['status'] != ('completed' if regenerate else 'failed'):
            return None
        job_id, stamp = uuid.uuid4().hex, now()
        kind = json.loads(db.execute('SELECT data FROM traces WHERE id=?', (trace_id,)).fetchone()['data'])['kind']
        db.execute("INSERT INTO jobs VALUES (?,?,?,'queued','等待调度',0,0,NULL,NULL,?,?)", (job_id, trace_id, 'demo' if kind=='demo' else 'live', stamp, stamp))
        db.execute('INSERT INTO run_attempts (run_id,trace_id,job_id,attempt,fresh_cache) VALUES (?,?,?,?,?)',
                   (run_id, trace_id, job_id, latest['attempt']+1 if latest else 1,
                    int(regenerate or bool(latest and latest['fresh_cache']))))
        return job_id

    def regenerate_attempt(self, job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assert_job_writable(job_id, db)
            attempt = db.execute('''SELECT a.run_id,a.trace_id,j.status,j.mode FROM run_attempts a
                JOIN jobs j ON j.id=a.job_id WHERE a.job_id=?''', (job_id,)).fetchone()
            if not attempt or attempt['status'] != 'completed' or attempt['mode'] != 'live':
                raise ValueError('仅已完成的正式轨迹诊断可重新生成')
            if db.execute("SELECT 1 FROM feedback_runs WHERE job_id=? AND status IN ('queued','running')", (job_id,)).fetchone():
                raise ValueError('请等待本条轨迹的改进建议生成完成')
            return self._enqueue_item(db, attempt['run_id'], attempt['trace_id'], regenerate=True)

    def uses_fresh_diagnosis_cache(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT fresh_cache FROM run_attempts WHERE job_id=?', (job_id,)).fetchone()
            return bool(row and row['fresh_cache'])

    def enqueue_items(self, run_id, trace_ids, action):
        created = []
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._assert_latest(db, run_id)
            members = {r['trace_id'] for r in db.execute('SELECT trace_id FROM run_items WHERE run_id=?', (run_id,))}
            if not set(trace_ids) <= members:
                raise ValueError('所选轨迹不属于当前分析记录')
            for trace_id in dict.fromkeys(trace_ids):
                latest = db.execute('SELECT j.status FROM jobs j JOIN run_attempts a ON a.job_id=j.id WHERE a.run_id=? AND a.trace_id=? ORDER BY a.attempt DESC LIMIT 1', (run_id, trace_id)).fetchone()
                if action=='retry_failed' and (not latest or latest['status']!='failed'):
                    continue
                if action=='pending' and latest:
                    continue
                job_id = self._enqueue_item(db, run_id, trace_id)
                if job_id:
                    created.append(job_id)
        return created

    def run_rows(self, run_id):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("""
            SELECT t.id,t.title,t.created_at,x.position,
             json_extract(t.data,'$.dataset') dataset,json_extract(t.data,'$.task_id') task_id,
             json_extract(t.data,'$.source') source,json_extract(t.data,'$.outcome') outcome,
             json_extract(t.data,'$.agent_identity.name') agent_name,
             json_extract(t.data,'$.kind') kind,json_array_length(t.data,'$.messages') steps,
             COALESCE(json_extract(t.data,'$.input_format'),
                      CASE WHEN json_type(t.data,'$.otel')='object' THEN 'otel_genai' ELSE 'messages' END) input_format,
             j.id job_id,COALESCE(j.status,'pending') diagnosis,j.stage,j.progress,j.error,j.updated_at,
             a.attempt attempts,j.mode,json_extract(j.result,'$.schema_version') schema_version,
             json_extract(j.result,'$.algorithm') algorithm,
             json_type(j.result,'$.engine_runs')='object' multi_engine,
             json_extract(j.result,'$.attribution.critical_step') critical_step,
             json_extract(j.result,'$.attribution.category') category,
             json_extract(j.result,'$.attribution.summary') summary,
             f.status feedback_status,f.error feedback_error,
             json_extract(v.data,'$.decision') review_status,
             json_extract(j.result,'$.comparison.state') comparison_state,
             json_extract(j.result,'$.comparison.primary') comparison_primary,
             json_extract(j.result,'$.comparison.engines') comparison_engines,
             json_extract(j.result,'$.synthesis.status') synthesis_status,
             json_extract(j.result,'$.synthesis.summary') synthesis_summary,
             json_extract(j.result,'$.synthesis.assessments') candidate_assessments,
             (SELECT json_group_object(key,json_extract(value,'$.report.attribution'))
              FROM json_each(j.result,'$.engine_runs') WHERE json_extract(value,'$.status')='completed') candidate_attributions,
             v.data review_data
            FROM run_items x JOIN traces t ON t.id=x.trace_id
            LEFT JOIN run_attempts a ON a.job_id=(SELECT job_id FROM run_attempts WHERE run_id=x.run_id AND trace_id=x.trace_id ORDER BY attempt DESC LIMIT 1)
            LEFT JOIN jobs j ON j.id=a.job_id
            LEFT JOIN feedback_runs f ON f.rowid=(SELECT rowid FROM feedback_runs WHERE job_id=j.id ORDER BY created_at DESC,rowid DESC LIMIT 1)
            LEFT JOIN reviews v ON v.rowid=(SELECT rowid FROM reviews WHERE job_id=j.id ORDER BY created_at DESC,rowid DESC LIMIT 1)
            WHERE x.run_id=? ORDER BY x.position
            """, (run_id,))]
        for row in rows:
            comparison_state = row.pop('comparison_state')
            primary = row.pop('comparison_primary')
            participants = row.pop('comparison_engines')
            synthesis_status = row.pop('synthesis_status')
            synthesis_summary = row.pop('synthesis_summary')
            assessments = row.pop('candidate_assessments')
            alternatives = row.pop('candidate_attributions')
            review = row.pop('review_data')
            trace_feedback = row.pop('multi_engine') or row['algorithm'] in {'AgentDebugX', 'CHIEF'}
            if row['diagnosis'] == 'completed':
                row['review_status'] = review_status(comparison_state, row['review_status'])
            row['attempts'] = row['attempts'] or 0
            row['feedback_applicable'] = False
            row['step_pending_review'] = False
            if row['diagnosis'] != 'completed' or row['outcome'] != 'failed':
                continue
            original_step = row['critical_step']
            if review:
                chosen = json.loads(review)
            elif comparison_state and comparison_state != 'succeeded':
                comparison = {'state': comparison_state, 'primary': primary, 'engines': json.loads(participants or 'null')}
                sources = participating_attributions(comparison, row, json.loads(alternatives or '{}'))
                synthesis = resolve_synthesis(comparison, sources, {
                    'status': synthesis_status, 'summary': synthesis_summary,
                    'assessments': json.loads(assessments or '{}'),
                }, step_count=row['steps'])
                chosen = {
                    'critical_step': synthesis['critical_step'],
                    'category': sources.get(synthesis['preferred_engine'], {}).get('category', ''),
                    'summary': synthesis['summary'],
                }
            else:
                chosen = row
            row.update({key: chosen.get(key) for key in ('critical_step', 'category', 'summary')})
            feedback_step = row['critical_step'] if trace_feedback else original_step
            row['feedback_applicable'] = (row['mode'] == 'live' and row['schema_version'] == 2
                and (not trace_feedback or row['review_status'] != 'rejected')
                and type(feedback_step) is int and 0 <= feedback_step < row['steps'])
        return rows

    def item_attempts(self, run_id, trace_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT j.id,j.status,j.stage,j.error,j.created_at,a.attempt FROM run_attempts a JOIN jobs j ON j.id=a.job_id WHERE a.run_id=? AND a.trace_id=? ORDER BY a.attempt DESC', (run_id, trace_id))]

    def auto_feedback_jobs(self):
        with self.connect() as db:
            return [r['job_id'] for r in db.execute("""SELECT a.job_id FROM run_attempts a JOIN analysis_runs r ON r.id=a.run_id
             JOIN jobs j ON j.id=a.job_id WHERE r.auto_feedback=1 AND j.status='completed'
             AND r.number=(SELECT MAX(number) FROM analysis_runs WHERE task_id=r.task_id)
             AND NOT EXISTS(SELECT 1 FROM feedback_runs WHERE job_id=j.id)""")]
