"""Delete workbench records without breaking shared trajectory or evidence links."""

import json
import logging
import re
import shutil

from pydantic import Field

from .models import Contract


logger = logging.getLogger(__name__)
ARTIFACT_KEY = 'deleted_artifact:'
RUN_ID = """COALESCE(json_extract(result,'$.provenance.run_id'),
                     json_extract(result,'$.engine_runs.trajdebug.provenance.run_id'))"""


class DeleteSelection(Contract):
    ids: list[str] = Field(min_length=1)


class DeletionStore:
    def delete_task(self, task_id):
        self._delete_one(task_id, self._delete_task)

    def delete_trace(self, trace_id):
        self._delete_one(trace_id, self._delete_trace)

    def delete_tasks(self, ids):
        return self._delete_selection(ids, self._delete_task, patterns_first=True)

    def delete_traces(self, ids):
        return self._delete_selection(ids, self._delete_trace)

    def _delete_one(self, record_id, remove):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            remove(db, record_id)
        self.purge_deleted_artifacts()

    def _delete_selection(self, ids, remove, *, patterns_first=False):
        ids = list(dict.fromkeys(ids))
        deleted, skipped = [], []
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if patterns_first:
                # A selected pattern task may own a reference to another selected task.
                patterns = {row[0] for row in db.execute("""
                    SELECT id FROM analysis_tasks WHERE kind='failure_patterns'
                    AND id IN (SELECT value FROM json_each(?))""", (json.dumps(ids),))}
                ids.sort(key=lambda record_id: record_id not in patterns)
            for record_id in ids:
                db.execute('SAVEPOINT selected_record')
                try:
                    remove(db, record_id)
                except (ValueError, LookupError) as exc:
                    db.execute('ROLLBACK TO selected_record')
                    skipped.append({'id': record_id, 'reason': str(exc)})
                else:
                    deleted.append(record_id)
                db.execute('RELEASE selected_record')
        self.purge_deleted_artifacts()
        return {'deleted_ids': deleted, 'skipped': skipped}

    def _delete_task(self, db, task_id):
        task = db.execute('SELECT kind FROM analysis_tasks WHERE id=?', (task_id,)).fetchone()
        if not task:
            raise LookupError('分析任务不存在')
        if task['kind'] == 'failure_patterns':
            run_ids = 'SELECT id FROM pattern_runs WHERE task_id=?'
            db.execute(f'DELETE FROM pattern_reviews WHERE run_id IN ({run_ids})', (task_id,))
            db.execute(f'DELETE FROM pattern_run_inputs WHERE run_id IN ({run_ids})', (task_id,))
            db.execute('DELETE FROM pattern_runs WHERE task_id=?', (task_id,))
        else:
            run_ids = 'SELECT id FROM analysis_runs WHERE task_id=?'
            if db.execute(f"""SELECT 1 FROM task_improvements WHERE run_id IN ({run_ids})
                AND status IN ('queued','running') LIMIT 1""", (task_id,)).fetchone():
                raise ValueError('仍有任务改进建议在排队或生成，请处理完成后再删除')
            job_ids = f'SELECT job_id FROM run_attempts WHERE run_id IN ({run_ids})'
            blocked = db.execute(f'''SELECT 1 FROM pattern_runs
                WHERE json_extract(scope,'$.task_id')=?
                UNION ALL SELECT 1 FROM pattern_run_inputs WHERE job_id IN ({job_ids})
                LIMIT 1''', (task_id, task_id)).fetchone()
            if blocked:
                raise ValueError('此任务的诊断已被失败模式分析引用，请先删除关联的失败模式分析任务')
            jobs = self._deletable_jobs(db, f'id IN ({job_ids})', task_id)
            db.execute(f'DELETE FROM run_attempts WHERE run_id IN ({run_ids})', (task_id,))
            self._delete_jobs(db, jobs)
            db.execute(f'DELETE FROM run_items WHERE run_id IN ({run_ids})', (task_id,))
            db.execute('DELETE FROM analysis_runs WHERE task_id=?', (task_id,))
        db.execute('DELETE FROM task_traces WHERE task_id=?', (task_id,))
        db.execute('DELETE FROM analysis_tasks WHERE id=?', (task_id,))

    def _delete_trace(self, db, trace_id):
        if not db.execute('SELECT 1 FROM traces WHERE id=?', (trace_id,)).fetchone():
            raise LookupError('轨迹不存在')
        linked = db.execute('''SELECT 1 FROM task_traces WHERE trace_id=?
            UNION ALL SELECT 1 FROM run_items WHERE trace_id=?
            UNION ALL SELECT 1 FROM pattern_run_inputs WHERE trace_id=? LIMIT 1''',
            (trace_id, trace_id, trace_id)).fetchone()
        if linked:
            raise ValueError('此轨迹仍被分析任务引用，请先删除关联任务再删除轨迹')
        self._delete_jobs(db, self._deletable_jobs(db, 'trace_id=?', trace_id))
        db.execute('DELETE FROM traces WHERE id=?', (trace_id,))

    @staticmethod
    def _deletable_jobs(db, condition, value):
        jobs = db.execute(f'''SELECT id, status, {RUN_ID} AS run_id,
            EXISTS (SELECT 1 FROM feedback_runs WHERE job_id=jobs.id
                AND status IN ('queued','running')) AS feedback_active FROM jobs WHERE {condition}''',
            (value,)).fetchall()
        if any(job['status'] in ('queued', 'running') or job['feedback_active'] for job in jobs):
            raise ValueError('仍有诊断或改进建议在排队或运行，请处理完成后再删除')
        return jobs

    @staticmethod
    def _delete_jobs(db, jobs):
        if not jobs:
            return
        ids = 'SELECT value FROM json_each(?)'
        params = (json.dumps([job['id'] for job in jobs]),)
        feedback_ids = [row[0] for row in db.execute(f'SELECT id FROM feedback_runs WHERE job_id IN ({ids})', params)]
        for table in ('reviews', 'feedback_runs', 'standalone_job_configs'):
            db.execute(f'DELETE FROM {table} WHERE job_id IN ({ids})', params)
        db.execute(f'DELETE FROM jobs WHERE id IN ({ids})', params)
        run_ids = {job['run_id'] for job in jobs if isinstance(job['run_id'], str)
                   and re.fullmatch(r'[0-9a-f]{32}', job['run_id'])}
        if run_ids:
            remaining = db.execute(f'''SELECT DISTINCT {RUN_ID}
                FROM jobs WHERE {RUN_ID} IN ({ids})''',
                (json.dumps(sorted(run_ids)),))
            run_ids.difference_update(row[0] for row in remaining)
        db.executemany('INSERT OR IGNORE INTO workspace_meta VALUES (?,?)',
                       [(ARTIFACT_KEY + kind + ':' + artifact_id, artifact_id)
                        for kind, values in (('runs', run_ids), ('feedback', feedback_ids))
                        for artifact_id in values if re.fullmatch(r'[0-9a-f]{32}', artifact_id)])

    def purge_deleted_artifacts(self):
        # SQLite owns the pending work so a failed filesystem cleanup can be retried.
        with self.connect() as db:
            pending = db.execute('SELECT key,value FROM workspace_meta WHERE key GLOB ?',
                                 (ARTIFACT_KEY + '*',)).fetchall()
        for row in pending:
            kind = row['key'].split(':')[1]
            artifact_id = row['value']
            if kind not in ('runs', 'feedback') or not re.fullmatch(r'[0-9a-f]{32}', artifact_id):
                continue
            try:
                shutil.rmtree(self.path.parent / 'trajdebug' / kind / artifact_id)
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning('Unable to clean up deleted artifact %s/%s', kind, artifact_id)
                continue
            with self.connect() as db:
                db.execute('DELETE FROM workspace_meta WHERE key=?', (row['key'],))
