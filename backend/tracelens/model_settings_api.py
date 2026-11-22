"""Manage and test OpenAI-compatible model connections."""

import json
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import Field, SecretStr, field_validator

from .models import Contract

MODEL_SETTINGS_PATH = '/api/model-configurations'
ENVIRONMENT_ID = 'environment'


class ModelConnectionInput(Contract):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = Field(default=SecretStr(''), max_length=4096)

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


class ModelConnectionCreate(ModelConnectionInput):
    make_default: bool = False


async def test_connection(base_url, model, api_key):
    url = base_url.rstrip('/')
    if not url.endswith('/chat/completions'):
        url += '/chat/completions'
    try:
        async with httpx.AsyncClient(timeout=25, trust_env=False, follow_redirects=False) as client:
            response = await client.post(url, headers={'Authorization': 'Bearer ' + (api_key or 'EMPTY')}, json={
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


def attach_model_settings_routes(app, connections, settings):
    router = APIRouter(prefix=MODEL_SETTINGS_PATH)

    @router.get('')
    def list_configurations():
        items = connections.list()
        default_id = next((item['id'] for item in items if item['is_default']), None)
        items = [{**item, 'has_key': bool(item['has_key']), 'is_default': bool(item['is_default'])} for item in items]
        if settings.ready:
            items.insert(0, {'id': ENVIRONMENT_ID, 'name': '环境配置', 'base_url': settings.base_url,
                             'model': settings.model, 'has_key': bool(settings.api_key),
                             'is_default': default_id is None})
        return {'items': items}

    @router.post('', status_code=201)
    def add_configuration(body: ModelConnectionCreate):
        connection_id = connections.add(name=body.name, base_url=body.base_url, model=body.model,
                                        api_key=body.api_key.get_secret_value(), make_default=body.make_default)
        return {'id': connection_id}

    @router.post('/test')
    async def test_new_configuration(body: ModelConnectionInput):
        return await test_connection(body.base_url, body.model, body.api_key.get_secret_value())

    @router.post('/{connection_id}/test')
    async def test_saved_configuration(connection_id: str):
        if connection_id == ENVIRONMENT_ID and settings.ready:
            return await test_connection(settings.base_url, settings.model, settings.api_key)
        connection = connections.get(connection_id)
        if connection is None:
            raise HTTPException(404, '模型配置不存在')
        return await test_connection(connection['base_url'], connection['model'], connection['api_key'])

    @router.post('/{connection_id}/default')
    def set_default_configuration(connection_id: str):
        if connection_id == ENVIRONMENT_ID:
            if not settings.ready:
                raise HTTPException(404, '环境配置不可用')
            connection_id = None
        if not connections.set_default(connection_id):
            raise HTTPException(404, '模型配置不存在')
        return {'ok': True}

    app.include_router(router)
