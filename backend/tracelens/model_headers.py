"""Apply user-supplied HTTP headers to the OpenAI-compatible client."""

import json


def parse_headers(value):
    try:
        parsed = json.loads(value or '{}')
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def apply_headers(model, headers):
    if not headers:
        return
    custom = getattr(model.client, '_custom_headers', None)
    if custom is None:
        custom = {}
        model.client._custom_headers = custom
    custom.update(headers)
