"""Workspace slots shared by sync and async model clients across worker processes."""

import asyncio
from contextlib import asynccontextmanager, closing, contextmanager
import os
from pathlib import Path
import sqlite3
import time

from filelock import FileLock, Timeout


MAX_MODEL_CONCURRENCY = 16
POLL_INTERVAL = 0.05
WORKSPACE_ENV = "TRACELENS_MODEL_SLOT_WORKSPACE"
LIMIT_ENV = "TRACELENS_MODEL_SLOT_LIMIT"


def model_slot_environment(settings):
    return {WORKSPACE_ENV: str(settings.data_dir.resolve()), LIMIT_ENV: str(settings.llm_concurrency)}


class ModelSlots:
    def __init__(self, workspace, default_limit):
        self.workspace = Path(workspace).resolve()
        self.default_limit = default_limit
        self.directory = self.workspace / ".model-slots"
        self.directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_settings(cls, settings):
        return cls(settings.data_dir, settings.llm_concurrency)

    @classmethod
    def from_environment(cls):
        workspace = os.getenv(WORKSPACE_ENV)
        return cls(workspace, int(os.environ[LIMIT_ENV])) if workspace else None

    def limit(self):
        database = self.workspace / "tracelens.sqlite3"
        if database.is_file():
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
                row = db.execute("""
                    SELECT json_extract(value, '$.llm_concurrency')
                    FROM workspace_meta WHERE key='runtime_settings_v1'
                """).fetchone()
            if row and type(row[0]) is int:
                return max(1, min(MAX_MODEL_CONCURRENCY, row[0]))
        return max(1, min(MAX_MODEL_CONCURRENCY, self.default_limit))

    def try_acquire(self):
        try:
            with FileLock(self.directory / "admission.lock", timeout=0):
                limit = self.limit()
                return self._reserve_slot(limit)
        except Timeout:
            return None
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY:
                return None
            raise

    def _reserve_slot(self, limit):
        candidate = None
        occupied = 0
        try:
            # Count every occupied slot, including slots above a newly lowered limit.
            for number in range(MAX_MODEL_CONCURRENCY):
                slot = FileLock(self.directory / f"{number}.lock", timeout=0)
                try:
                    slot.acquire()
                except Timeout:
                    occupied += 1
                    if occupied >= limit:
                        return None
                else:
                    if candidate is None:
                        candidate = slot
                    else:
                        slot.release()
            admitted, candidate = candidate, None
            return admitted
        finally:
            if candidate:
                candidate.release()

    @contextmanager
    def request(self):
        while (slot := self.try_acquire()) is None:
            time.sleep(POLL_INTERVAL)
        try:
            yield
        finally:
            slot.release()

    @asynccontextmanager
    async def async_request(self):
        while (slot := self.try_acquire()) is None:
            await asyncio.sleep(POLL_INTERVAL)
        try:
            yield
        finally:
            slot.release()

    def attach(self, client):
        send = client.send

        def limited_send(*args, **kwargs):
            with self.request():
                return send(*args, **kwargs)

        client.send = limited_send

    def attach_async(self, client):
        send = client.send

        async def limited_send(*args, **kwargs):
            async with self.async_request():
                return await send(*args, **kwargs)

        client.send = limited_send
