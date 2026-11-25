"""Apply user-supplied HTTP headers to the OpenAI-compatible client."""

import json


def parse_headers(value):
    try:
        parsed = json.loads(value or '{}')
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def merge_headers(defaults, overrides):
    """Apply overrides case-insensitively while preserving default spelling."""
    result = dict(defaults)
    names = {name.lower(): name for name in result}
    for name, value in (overrides or {}).items():
        result[names.setdefault(name.lower(), name)] = value
    return result


def apply_headers(model, headers):
    if not headers:
        return
    # The OpenAI SDK merges dictionaries before converting them to HTTP headers.
    names = {name.lower(): name for name in model.client.default_headers}
    names['authorization'] = 'Authorization'  # Injected separately by the SDK's auth handler.
    normalized = {names.get(name.lower(), name): value for name, value in headers.items()}
    model.client._custom_headers = merge_headers(model.client._custom_headers, normalized)
