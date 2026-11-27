"""Manage and test OpenAI-compatible model connections."""

import json
import re
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import Field, SecretStr, field_validator

from .execution import runtime_snapshot
from .model_connections import ModelPurpose
from .model_headers import merge_headers
from .model_concurrency import ModelSlots
from .models import Contract
from .runtime_settings_api import RuntimeSettingsInput

MODEL_SETTINGS_PATH = '/api/model-configurations'
ENVIRONMENT_ID = 'environment'


class ModelConnectionInput(Contract):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = Field(default=SecretStr(''), max_length=4096)
    headers: dict[str, str] | None = Field(default=None, max_length=40)

    @field_validator('name', 'model', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('base_url')
    @classmethod
    def validate_url(cls, value):
        url = value.strip().rstrip('/')
        try:
            parts = urlsplit(url)
            _ = parts.port
            if (parts.scheme not in {'http', 'https'} or not parts.hostname or
                    parts.username is not None or '?' in url or '#' in url or
                    any(ch.isspace() or ch == '\\' or ord(ch) < 32 for ch in url)):
                raise ValueError
        except ValueError:
            raise HTTPException(422, '请输入有效的 HTTP 或 HTTPS 接口地址，地址中请勿包含凭据、查询参数或片段') from None
        return url.removesuffix('/chat/completions').rstrip('/')

    @field_validator('api_key')
    @classmethod
    def validate_key(cls, value):
        key = value.get_secret_value().strip()
        if any(not 33 <= ord(ch) <= 126 for ch in key):
            raise HTTPException(422, 'API 密钥不能包含空格或控制字符')
        return SecretStr(key)

    @field_validator('headers', mode='before')
    @classmethod
    def validate_headers(cls, value):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError('自定义 Header 必须是 JSON 对象')
        normalized = {}
        names = set()
        name_pattern = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
        for raw_name, raw_value in value.items():
            name = raw_name.strip() if isinstance(raw_name, str) else ''
            header_value = raw_value.strip() if isinstance(raw_value, str) else None
            if not name or not name_pattern.fullmatch(name) or len(name) > 200 or name.lower() in names:
                raise ValueError('自定义 Header 名称无效或重复')
            if header_value is None or len(header_value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in header_value):
                raise ValueError('自定义 Header 值必须是字符串，且不能包含控制字符')
            names.add(name.lower())
            normalized[name] = header_value
        return normalized


class ModelConnectionWrite(ModelConnectionInput):
    make_default: bool = False
    runtime: RuntimeSettingsInput | None = None


async def test_connection(base_url, model, api_key, headers, settings):
    url = base_url.rstrip('/')
    if not url.endswith('/chat/completions'):
        url += '/chat/completions'
    try:
        async with httpx.AsyncClient(timeout=25, trust_env=False, follow_redirects=False) as client:
            ModelSlots.for_settings(settings).attach_async(client)
            request_headers = merge_headers({'Authorization': 'Bearer ' + (api_key or 'EMPTY'),
                                             'Content-Type': 'application/json'}, headers)
            response = await client.post(url, headers=request_headers, json={
                'model': model,
                'messages': [{'role': 'user', 'content': 'Return only the JSON object {"ok": true}.'}],
                'response_format': {'type': 'json_object'},
                'max_tokens': 1024,
            })
    except httpx.TimeoutException:
        raise HTTPException(504, '模型服务响应超时，请稍后重试') from None
    except (httpx.RequestError, httpx.InvalidURL, ValueError):
        raise HTTPException(502, '无法连接模型服务，请检查接口地址和网络连接') from None
    if not response.is_success:
        detail = {
            400: '模型服务拒绝了请求，请确认模型支持 Chat Completions 和 JSON 输出',
            401: '模型服务认证失败，请检查 API 密钥',
            403: '模型服务拒绝访问，请检查 API 密钥权限或模型访问权限',
            404: '模型或接口不存在，请核对接口地址与模型标识',
            422: '模型服务无法处理请求，请确认模型支持 Chat Completions 和 JSON 输出',
            429: '模型服务已限流或额度不足，请检查账户状态后重试',
        }.get(response.status_code, f'模型服务返回 HTTP {response.status_code}，请检查服务状态与接口地址')
        raise HTTPException(502, detail)
    try:
        content = response.json()['choices'][0]['message']['content']
        if not isinstance(json.loads(content), dict):
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, '模型未返回有效的 JSON 对象，请检查所选模型的 JSON 输出能力') from None
    return {'ok': True}


def attach_model_settings_routes(app, connections, settings, queue):
    router = APIRouter(prefix=MODEL_SETTINGS_PATH)

    def runtime_patch(body, purpose):
        if body.runtime is None:
            return None
        if purpose != 'diagnosis':
            raise HTTPException(422, '运行参数适用于诊断分析模型')
        defaults = runtime_snapshot(settings)
        return {key: defaults[key] if value is None else value
                for key, value in body.runtime.model_dump(exclude_unset=True).items()}

    def with_runtime(item, purpose):
        runtime = item.pop('runtime', {})
        if purpose == 'diagnosis':
            item['runtime'] = {**runtime_snapshot(settings), **runtime}
        return item

    @router.get('')
    def list_configurations(purpose: ModelPurpose = 'diagnosis'):
        items = connections.list(purpose)
        default_id = next((item['id'] for item in items if item['is_default']), None)
        items = [with_runtime({**item, 'has_key': bool(item['has_key']), 'has_headers': bool(item['has_headers']),
                               'is_default': bool(item['is_default'])}, purpose) for item in items]
        if purpose == 'diagnosis' and settings.ready:
            items.insert(0, {'id': ENVIRONMENT_ID, 'name': '环境配置', 'base_url': settings.base_url,
                             'model': settings.model, 'has_key': bool(settings.api_key),
                             'has_headers': bool(settings.headers),
                             'is_default': default_id is None, 'runtime': runtime_snapshot(settings)})
        return ({'items': items, 'runtime_defaults': runtime_snapshot(settings)}
                if purpose == 'diagnosis' else {'items': items})

    @router.post('', status_code=201)
    async def add_configuration(body: ModelConnectionWrite, purpose: ModelPurpose = 'diagnosis'):
        patch = runtime_patch(body, purpose)
        runtime = {**runtime_snapshot(settings), **(patch or {})} if purpose == 'diagnosis' else None
        connection_id = connections.add(name=body.name, base_url=body.base_url, model=body.model,
                                        api_key=body.api_key.get_secret_value(), headers=body.headers,
                                        make_default=body.make_default, purpose=purpose, runtime=runtime)
        if purpose == 'diagnosis':
            await queue.apply_settings()
        return {'id': connection_id}

    @router.get('/{connection_id}')
    def get_configuration(connection_id: str, purpose: ModelPurpose = 'diagnosis'):
        connection = connections.get(connection_id, purpose)
        if connection is None:
            raise HTTPException(404, '模型配置不存在')
        fields = {key: connection[key] for key in ('id', 'name', 'base_url', 'model', 'runtime')}
        return with_runtime({**fields, 'has_key': bool(connection['api_key']), 'has_headers': bool(connection['headers']),
                             'is_default': bool(connection['is_default'])}, purpose)

    @router.put('/{connection_id}')
    async def update_configuration(connection_id: str, body: ModelConnectionWrite, purpose: ModelPurpose = 'diagnosis'):
        if not connections.update(connection_id, name=body.name, base_url=body.base_url, model=body.model,
                                  api_key=body.api_key.get_secret_value(), headers=body.headers,
                                  make_default=body.make_default, purpose=purpose, runtime=runtime_patch(body, purpose)):
            raise HTTPException(404, '模型配置不存在')
        if purpose == 'diagnosis':
            await queue.apply_settings()
        return {'ok': True}

    @router.delete('/{connection_id}')
    async def delete_configuration(connection_id: str, purpose: ModelPurpose = 'diagnosis'):
        if not connections.delete(connection_id, purpose):
            raise HTTPException(404, '模型配置不存在')
        if purpose == 'diagnosis':
            await queue.apply_settings()
        return {'ok': True}

    @router.post('/test')
    async def test_new_configuration(body: ModelConnectionInput):
        return await test_connection(body.base_url, body.model, body.api_key.get_secret_value(), body.headers, settings)

    @router.post('/{connection_id}/test')
    async def test_saved_configuration(connection_id: str, purpose: ModelPurpose = 'diagnosis'):
        if connection_id == ENVIRONMENT_ID and purpose == 'diagnosis' and settings.ready:
            return await test_connection(settings.base_url, settings.model, settings.api_key, settings.headers, settings)
        connection = connections.get(connection_id, purpose)
        if connection is None:
            raise HTTPException(404, '模型配置不存在')
        return await test_connection(connection['base_url'], connection['model'], connection['api_key'], connection['headers'], settings)

    @router.post('/{connection_id}/default')
    async def set_default_configuration(connection_id: str, purpose: ModelPurpose = 'diagnosis'):
        if connection_id == ENVIRONMENT_ID and purpose == 'diagnosis':
            if not settings.ready:
                raise HTTPException(404, '环境配置不可用')
            connection_id = None
        if not connections.set_default(connection_id, purpose):
            raise HTTPException(404, '模型配置不存在')
        if purpose == 'diagnosis':
            await queue.apply_settings()
        return {'ok': True}

    app.include_router(router)
