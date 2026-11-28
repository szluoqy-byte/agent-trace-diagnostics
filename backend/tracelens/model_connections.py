"""Local model credentials, kept outside the workspace database and exports."""

from contextlib import contextmanager, closing
import json
import os
import sqlite3
from uuid import uuid4
from typing import Literal


ModelPurpose = Literal['diagnosis', 'playground']
DATABASE_NAME = 'model-connections.sqlite3'
ENVIRONMENT_ID = 'environment'


class ModelConnections:
    def __init__(self, directory):
        self.path = directory / DATABASE_NAME
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("""
                CREATE TABLE IF NOT EXISTS model_connections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    headers TEXT NOT NULL DEFAULT '{}',
                    is_default INTEGER NOT NULL DEFAULT 0,
                    purpose TEXT NOT NULL DEFAULT 'diagnosis',
                    runtime TEXT,
                    request_parameters TEXT NOT NULL DEFAULT '{}'
                )
            """)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(model_connections)')}
            if 'headers' not in columns:
                db.execute("ALTER TABLE model_connections ADD COLUMN headers TEXT NOT NULL DEFAULT '{}'")
            if 'purpose' not in columns:
                db.execute("ALTER TABLE model_connections ADD COLUMN purpose TEXT NOT NULL DEFAULT 'diagnosis'")
            if 'runtime' not in columns:
                db.execute('ALTER TABLE model_connections ADD COLUMN runtime TEXT')
            if 'request_parameters' not in columns:
                db.execute("ALTER TABLE model_connections ADD COLUMN request_parameters TEXT NOT NULL DEFAULT '{}'")
            db.execute('DROP INDEX IF EXISTS one_default_model_connection')
            db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS one_default_model_per_purpose
                          ON model_connections(purpose) WHERE is_default=1''')

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def migrate_runtime(self, defaults):
        with self.connect() as db:
            db.execute("UPDATE model_connections SET runtime=? WHERE purpose='diagnosis' AND runtime IS NULL",
                       (json.dumps(defaults),))

    def list(self, purpose: ModelPurpose = 'diagnosis'):
        with self.connect() as db:
            return [{**dict(row), 'runtime': json.loads(row['runtime'] or '{}'),
                     'request_parameters': json.loads(row['request_parameters'])} for row in db.execute("""
                SELECT id,name,base_url,model,api_key != '' AS has_key,headers != '{}' AS has_headers,is_default,runtime,request_parameters
                FROM model_connections WHERE purpose=? ORDER BY rowid""", (purpose,))]

    @staticmethod
    def _decode(row):
        value = dict(row)
        try:
            value['headers'] = json.loads(value.get('headers') or '{}')
        except (TypeError, ValueError):
            value['headers'] = {}
        value['runtime'] = json.loads(value['runtime'] or '{}')
        value['request_parameters'] = json.loads(value['request_parameters'])
        return value

    def get(self, connection_id, purpose: ModelPurpose = 'diagnosis'):
        with self.connect() as db:
            row = db.execute('SELECT * FROM model_connections WHERE id=? AND purpose=?', (connection_id, purpose)).fetchone()
            return self._decode(row) if row else None

    def default(self, purpose: ModelPurpose = 'diagnosis'):
        with self.connect() as db:
            row = db.execute('SELECT * FROM model_connections WHERE is_default=1 AND purpose=?', (purpose,)).fetchone()
            return self._decode(row) if row else None

    def add(self, *, name, base_url, model, api_key, headers=None, make_default=False,
            purpose: ModelPurpose = 'diagnosis', runtime=None, request_parameters=None):
        connection_id = uuid4().hex
        with self.connect() as db:
            if make_default:
                db.execute('UPDATE model_connections SET is_default=0 WHERE is_default=1 AND purpose=?', (purpose,))
            db.execute('''INSERT INTO model_connections
                          (id,name,base_url,model,api_key,headers,is_default,purpose,runtime,request_parameters)
                          VALUES (?,?,?,?,?,?,?,?,?,?)''',
                       (connection_id, name, base_url, model, api_key,
                        json.dumps(headers or {}, ensure_ascii=False, sort_keys=True), int(make_default), purpose,
                        json.dumps(runtime) if runtime is not None else None,
                        json.dumps(request_parameters or {}, ensure_ascii=False, sort_keys=True)))
        return connection_id

    def update(self, connection_id, *, name, base_url, model, api_key=None, headers=None, make_default=False,
               purpose: ModelPurpose = 'diagnosis', runtime=None, request_parameters=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT api_key,headers,runtime,request_parameters FROM model_connections WHERE id=? AND purpose=?',
                             (connection_id, purpose)).fetchone()
            if row is None:
                return False
            stored_headers = json.loads(row['headers'] or '{}')
            next_key = api_key if api_key else row['api_key']
            next_headers = stored_headers if headers is None else headers
            if make_default:
                db.execute('UPDATE model_connections SET is_default=0 WHERE is_default=1 AND purpose=?', (purpose,))
            next_runtime = (json.dumps({**json.loads(row['runtime'] or '{}'), **runtime})
                            if runtime is not None else row['runtime'])
            next_parameters = (json.dumps(request_parameters, ensure_ascii=False, sort_keys=True)
                               if request_parameters is not None else row['request_parameters'])
            db.execute('''UPDATE model_connections
                          SET name=?,base_url=?,model=?,api_key=?,headers=?,is_default=?,runtime=?,request_parameters=?
                          WHERE id=?''',
                       (name, base_url, model, next_key,
                        json.dumps(next_headers, ensure_ascii=False, sort_keys=True), int(make_default), next_runtime,
                        next_parameters, connection_id))
            return True

    def update_default_runtime(self, changes):
        with self.connect() as db:
            row = db.execute("""UPDATE model_connections
                                SET runtime=json_patch(COALESCE(runtime,'{}'),?)
                                WHERE purpose='diagnosis' AND is_default=1 RETURNING runtime""",
                             (json.dumps(changes),)).fetchone()
            return json.loads(row['runtime']) if row else None

    def delete(self, connection_id, purpose: ModelPurpose = 'diagnosis'):
        with self.connect() as db:
            result = db.execute('DELETE FROM model_connections WHERE id=? AND purpose=?', (connection_id, purpose))
            return result.rowcount > 0

    def set_default(self, connection_id, purpose: ModelPurpose = 'diagnosis'):
        """A null ID selects the environment configuration."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if connection_id and not db.execute('SELECT 1 FROM model_connections WHERE id=? AND purpose=?',
                                                (connection_id, purpose)).fetchone():
                return False
            db.execute('UPDATE model_connections SET is_default=0 WHERE is_default=1 AND purpose=?', (purpose,))
            if connection_id:
                db.execute('UPDATE model_connections SET is_default=1 WHERE id=?', (connection_id,))
            return True
