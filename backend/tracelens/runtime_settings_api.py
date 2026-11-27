"""Manage diagnosis runtime settings for the active or environment model."""

from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from .execution import active_execution, runtime_snapshot
from .models import Contract

RUNTIME_SETTINGS_PATH = '/api/runtime-settings'


class RuntimeSettingsInput(Contract):
    concurrency: int | None = Field(default=None, ge=1, le=8)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    timeout: float | None = Field(default=None, ge=5, le=600)
    llm_concurrency: int | None = Field(default=None, ge=1, le=16)
    max_tokens: int | None = Field(default=None, ge=1024, le=131072)
    cluster_json_mode: bool | None = None


def attach_runtime_settings_routes(app, store, settings, queue):
    router = APIRouter(prefix=RUNTIME_SETTINGS_PATH)

    @router.get('')
    def get_runtime_settings(scope: Literal['active', 'environment'] = 'active'):
        execution = settings if scope == 'environment' else active_execution(store, settings)[0]
        return runtime_snapshot(execution)

    @router.put('')
    async def update_runtime_settings(body: RuntimeSettingsInput, scope: Literal['active', 'environment'] = 'active'):
        changes = body.model_dump(exclude_none=True)
        updated = store.model_connections.update_default_runtime(changes) if scope == 'active' else None
        if updated is not None:
            values = {**runtime_snapshot(settings), **updated}
        else:
            values = {**runtime_snapshot(settings), **changes}
            store.save_runtime_settings(values)
            for key, value in values.items():
                setattr(settings, key, value)
        await queue.apply_settings()
        return values

    app.include_router(router)
