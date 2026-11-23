"""Persist and apply workspace-level diagnosis runtime settings."""

from fastapi import APIRouter
from pydantic import Field

from .execution import RUNTIME_FIELDS, runtime_snapshot
from .models import Contract

RUNTIME_SETTINGS_PATH = '/api/runtime-settings'


class RuntimeSettingsInput(Contract):
    concurrency: int = Field(ge=1, le=8)
    max_retries: int = Field(ge=0, le=5)
    timeout: float = Field(ge=5, le=600)
    llm_concurrency: int = Field(ge=1, le=16)
    max_tokens: int = Field(ge=1024, le=131072)
    cluster_json_mode: bool


def attach_runtime_settings_routes(app, store, settings, queue):
    router = APIRouter(prefix=RUNTIME_SETTINGS_PATH)

    @router.get('')
    def get_runtime_settings():
        return runtime_snapshot(settings)

    @router.put('')
    async def update_runtime_settings(body: RuntimeSettingsInput):
        values = body.model_dump()
        store.save_runtime_settings(values)
        for key in RUNTIME_FIELDS:
            setattr(settings, key, values[key])
        await queue.apply_settings()
        return runtime_snapshot(settings)

    app.include_router(router)
