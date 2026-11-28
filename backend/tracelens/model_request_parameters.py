"""Validate connection-level additions to a Chat Completions JSON body."""

import json


RESERVED_PARAMETERS = frozenset({
    "model", "messages", "input", "prompt", "stream", "stream_options", "n",
    "max_tokens", "max_completion_tokens", "response_format",
    "tools", "tool_choice", "parallel_tool_calls", "functions", "function_call",
    "api_key", "api_base", "base_url", "url", "headers", "extra_headers", "authorization",
    "extra_body", "extra_query", "timeout",
})


def validate_request_parameters(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("自定义请求参数须为 JSON 对象")
    if any(not key or key.strip() != key for key in value):
        raise ValueError("自定义请求参数名称不能为空或包含首尾空格")
    reserved = RESERVED_PARAMETERS.intersection(key.lower() for key in value)
    if reserved:
        raise ValueError("以下请求参数由工作台管理：" + "、".join(sorted(reserved)))
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("自定义请求参数须为有效的 JSON 对象") from None
    return value
