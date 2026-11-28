"""Prepare recorded chat inputs and run a single Playground model request."""

import json
from time import monotonic
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, ValidationError

from .deepagent import content_text, decoded, model_choice
from .execution_view import execution_view
from .llm import MODEL_HTTP_HINTS
from .model_concurrency import ModelSlots
from .model_headers import merge_headers
from .models import Contract
from .otel_genai import INPUT, SYSTEM, _attributes, _parts


class PlaygroundMessage(Contract):
    role: Literal["system", "developer", "user", "assistant", "tool", "function"]
    content: str | list[dict] | None = None
    name: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    function_call: dict | None = None


class PlaygroundParameters(Contract):
    messages: list[PlaygroundMessage] = Field(min_length=1, max_length=20000)
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None
    functions: list[dict] | None = None
    function_call: str | dict | None = None
    parallel_tool_calls: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=131072)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    seed: int | None = None
    stop: str | list[str] | None = None
    response_format: dict | None = None


class PlaygroundRun(Contract):
    model_connection_id: str = Field(min_length=1, max_length=100)
    parameters: PlaygroundParameters


def captured_parameters(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("messages"), list) or not raw["messages"]:
        return None
    # Provider endpoint, credentials, streaming and other transport options belong to the workbench.
    body = {key: raw[key] for key in PlaygroundParameters.model_fields if key in raw and key != "messages"}
    messages = []
    for item in raw["messages"]:
        if not isinstance(item, dict):
            return None
        try:
            message = PlaygroundMessage.model_validate(
                {key: item[key] for key in PlaygroundMessage.model_fields if key in item}
            )
        except ValidationError:
            return None
        messages.append(message.model_dump(exclude_none=True))
    # Keep the captured request usable even when its provider logged incompatible sampling options.
    for key, value in list(body.items()):
        try:
            PlaygroundParameters.model_validate({"messages": [messages[0]], key: value})
        except ValidationError:
            body.pop(key)
    return {"messages": messages, **body}


def otel_chat_message(message):
    if "parts" not in message:
        return [message]
    role = message["role"]
    text = []
    calls = []
    results = []
    for part in message["parts"]:
        if part.get("type") == "text":
            text.append(part["content"])
        elif part.get("type") == "tool_call":
            if not part.get("id"):
                raise ValueError("missing tool call id")
            calls.append({"id": part["id"], "type": "function", "function": {
                "name": part["name"], "arguments": content_text(part.get("arguments")),
            }})
        elif part.get("type") == "tool_call_response":
            if not part.get("id"):
                raise ValueError("missing tool response id")
            results.append({"role": "tool", "tool_call_id": part["id"], "content": content_text(part.get("response"))})
    if results:
        return results
    item = {"role": role, "content": "\n".join(text) if text or not calls else None}
    if calls:
        item["tool_calls"] = calls
    if message.get("name"):
        item["name"] = message["name"]
    return [item]


def otel_request(call):
    if call["kind"] in {"execute_tool", "invoke_agent", "invoke_workflow", "create_agent", "embeddings"}:
        return None
    attrs = _attributes(call["raw"].get("attributes", []))
    inputs = decoded(attrs.get(INPUT))
    if not isinstance(inputs, list) or not inputs:
        return None
    messages = [item for message in inputs for item in otel_chat_message(message)]
    if SYSTEM in attrs:
        system = {"role": "system", "content": _parts(decoded(attrs[SYSTEM]))}
        if any(system[field] != messages[0].get(field) for field in system):
            messages.insert(0, system)
    request = {"messages": messages}
    if isinstance(definitions := decoded(attrs.get("gen_ai.tool.definitions")), list):
        tools = []
        for item in definitions:
            if not isinstance(item, dict) or item.get("type", "function") != "function":
                continue
            function = item.get("function", item)
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                tools.append({"type": "function", "function": {
                    key: function[key] for key in ("name", "description", "parameters", "strict") if key in function
                }})
        if tools:
            request["tools"] = tools
    for field in ("temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty", "seed"):
        if (name := "gen_ai.request." + field) in attrs:
            request[field] = attrs[name]
    return request


def output_fields(message):
    if not isinstance(message, dict):
        return {}
    return {key: message[key] for key in ("content", "tool_calls", "function_call", "refusal")
            if key in message}


def captured_output(call, is_otel):
    raw = call["output"]
    if raw is None:
        return None
    body = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, indent=2)
    if not body.strip():
        return None
    try:
        value = json.loads(body)
        is_json = True
    except ValueError:
        value = body
        is_json = False
    message = {}
    if is_otel and value == []:
        message = {"content": None}
    elif is_otel and isinstance(value, list) and isinstance(value[0], dict):
        # Compare the first recorded response, like a new Chat Completions result.
        try:
            message = output_fields(otel_chat_message(value[0])[0])
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    elif isinstance(value, dict):
        message = output_fields(model_choice(value).get("message", value.get("message", value)))
    return {"body": body, "is_json": is_json, "format": "otel_genai" if is_otel else "deepagent",
            "message": message or {"content": value}}


def trace_request(trace, span_id):
    data = trace["data"]
    is_otel = bool(data.get("otel"))
    calls = []
    chosen = None
    for number, call in enumerate(execution_view(data)["calls"], 1):
        try:
            request = otel_request(call) if is_otel else decoded(call["input"]) if call["kind"] == "LLM" else None
            request = captured_parameters(request)
        except (ValueError, KeyError, TypeError):
            continue
        if request is None:
            continue
        calls.append({"id": call["span_id"], "label": f"#{number} {call['title']}"})
        if span_id is None or span_id == call["span_id"]:
            chosen = (call, request)
    if span_id is not None and chosen is None:
        raise HTTPException(404, "此调用没有可直接带入的模型请求。")
    fallback = []
    if chosen is None:
        for message in data["messages"]:
            if message["role"] not in {"system", "developer", "user"}:
                break
            if message["content"].strip():
                fallback.append({"role": message["role"], "content": message["content"]})
        if not fallback and data.get("deepagent") and data.get("task_description", "").strip():
            fallback.append({"role": "user", "content": data["task_description"]})
    return {"title": trace["title"], "calls": calls, "selected_span": chosen[0]["span_id"] if chosen else None,
            "parameters": chosen[1] if chosen else {"messages": fallback},
            "original_output": captured_output(chosen[0], is_otel) if chosen else None}


async def run_model(connection, parameters, settings):
    payload = {**connection["request_parameters"], **parameters.model_dump(exclude_none=True)}
    if "max_tokens" not in payload and "max_completion_tokens" not in payload:
        payload["max_tokens"] = 2048
    headers = merge_headers({"Authorization": "Bearer " + (connection["api_key"] or "EMPTY")}, connection["headers"])
    url = connection["base_url"].rstrip("/").removesuffix("/chat/completions") + "/chat/completions"
    started = monotonic()
    try:
        async with httpx.AsyncClient(timeout=settings.timeout, trust_env=False, follow_redirects=False) as client:
            ModelSlots.for_settings(settings).attach_async(client)
            response = await client.post(url, headers=headers, json={**payload, "model": connection["model"], "stream": False})
    except httpx.TimeoutException:
        raise HTTPException(504, "模型服务响应超时，请稍后重试。") from None
    except (httpx.RequestError, httpx.InvalidURL, ValueError):
        raise HTTPException(502, "无法连接模型服务，请检查 Playground 模型配置。") from None
    if not response.is_success:
        hint = MODEL_HTTP_HINTS.get(response.status_code, "请检查所选模型和服务状态。")
        raise HTTPException(502, f"模型服务返回 HTTP {response.status_code}。{hint}")
    try:
        result = response.json()
        choice = result["choices"][0]
        message = choice["message"]
        output = output_fields(message)
        if not output:
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "模型服务未返回可读取的 Chat Completions 响应。") from None
    usage = result.get("usage") or {}
    return {"model": connection["model"], "duration_seconds": round(monotonic() - started, 2),
            "finish_reason": choice.get("finish_reason"), "message": output, "raw_response": response.text,
            "usage": {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                      if isinstance(usage, dict) and type(usage.get(key)) is int}}


def attach_playground_routes(app, store, settings):
    router = APIRouter(prefix="/api/playground")

    @router.get("/traces/{trace_id}")
    def prepare(trace_id: str, span_id: str | None = Query(default=None, max_length=500)):
        trace = store.trace(trace_id)
        if trace is None:
            raise HTTPException(404, "轨迹不存在。")
        return trace_request(trace, span_id)

    @router.post("/run")
    async def run(body: PlaygroundRun):
        connection = store.model_connections.get(body.model_connection_id, "playground")
        if connection is None:
            raise HTTPException(404, "所选 Playground 模型不存在，请重新选择。")
        if not any(message.content or message.tool_calls or message.function_call for message in body.parameters.messages):
            raise HTTPException(422, "请至少填写一条有内容的消息。")
        return await run_model(connection, body.parameters, settings)

    app.include_router(router)
