"""Local model credentials, kept outside the workspace database and exports."""

from contextlib import contextmanager, closing
import json
import os
import sqlite3
from uuid import uuid4


class ModelConnections:
    def __init__(self, directory):
        self.path = directory / 'model-connections.sqlite3'
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS model_connections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    headers TEXT NOT NULL DEFAULT '{}',
                    is_default INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_default_model_connection
                    ON model_connections(is_default) WHERE is_default=1;
            """)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(model_connections)')}
            if 'headers' not in columns:
                db.execute("ALTER TABLE model_connections ADD COLUMN headers TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT id,name,base_url,model,
                api_key != '' AS has_key,headers != '{}' AS has_headers,is_default
                FROM model_connections ORDER BY rowid""")]

    @staticmethod
    def _decode(row):
        value = dict(row)
        try:
            value['headers'] = json.loads(value.get('headers') or '{}')
        except (TypeError, ValueError):
            value['headers'] = {}
        return value

    def get(self, connection_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM model_connections WHERE id=?', (connection_id,)).fetchone()
            return self._decode(row) if row else None

    def default(self):
        with self.connect() as db:
            row = db.execute('SELECT * FROM model_connections WHERE is_default=1').fetchone()
            return self._decode(row) if row else None

    def add(self, *, name, base_url, model, api_key, headers=None, make_default=False):
        connection_id = uuid4().hex
        with self.connect() as db:
            if make_default:
                db.execute('UPDATE model_connections SET is_default=0 WHERE is_default=1')
            db.execute('INSERT INTO model_connections (id,name,base_url,model,api_key,headers,is_default) VALUES (?,?,?,?,?,?,?)',
                       (connection_id, name, base_url, model, api_key,
                        json.dumps(headers or {}, ensure_ascii=False, sort_keys=True), int(make_default)))
        return connection_id

    def set_default(self, connection_id):
        """A null ID selects the environment configuration."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if connection_id and not db.execute('SELECT 1 FROM model_connections WHERE id=?', (connection_id,)).fetchone():
                return False
            db.execute('UPDATE model_connections SET is_default=0 WHERE is_default=1')
            if connection_id:
                db.execute('UPDATE model_connections SET is_default=1 WHERE id=?', (connection_id,))
            return True
