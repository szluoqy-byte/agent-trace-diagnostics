"""Workbench extension point for localized diagnostic prompt templates."""


def diagnostic_prompt(client, key: str, default: str, **values) -> str:
    formatter = getattr(client, 'format_diagnostic_prompt', None)
    return formatter(key, default, values) if formatter else default
