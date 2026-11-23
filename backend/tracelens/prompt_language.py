"""Language routing for TrajDebug model prompts.

The upstream stages keep their JSON field names and taxonomy vocabulary stable.
This adapter adds a Chinese instruction layer when a task opts into Chinese
reasoning, while preserving technical terms such as Agent, tool, API and JSON.
"""

from __future__ import annotations

from functools import wraps


SUPPORTED_PROMPT_LANGUAGES = {"en", "zh"}

ZH_PROMPT_INSTRUCTION = """你正在执行 Agent 轨迹诊断。请用中文理解任务、轨迹、证据和错误因果关系，并用中文填写自然语言字段。
保留 Agent、tool、API、JSON、step、taxonomy_tag、instance_id 等业界通用术语和协议字段，不要翻译 JSON key、taxonomy tag、函数名、工具名、文件路径、URL、ID、数字、错误码或原文引用。
严格遵循后续提示词中的诊断定义、范围约束、证据引用要求和 JSON 输出结构。除协议字段和值域外，解释、reasoning、summary、suggestion 等文本使用中文。"""


def normalize_prompt_language(value: str | None) -> str:
    return "zh" if str(value or "en").strip().lower() in {"zh", "cn", "中文"} else "en"


def localize_text(text: str, language: str | None) -> str:
    """Prefix a single-prompt request with the selected language contract."""
    if normalize_prompt_language(language) != "zh":
        return text
    return f"{ZH_PROMPT_INSTRUCTION}\n\n{text}"


def localize_messages(messages: list[dict], language: str | None) -> list[dict]:
    """Add the language contract to a chat request without changing its schema."""
    if normalize_prompt_language(language) != "zh":
        return messages
    localized = [dict(message) for message in messages]
    if localized and localized[0].get("role") == "system":
        localized[0]["content"] = f"{ZH_PROMPT_INSTRUCTION}\n\n{localized[0].get('content', '')}"
    else:
        localized.insert(0, {"role": "system", "content": ZH_PROMPT_INSTRUCTION})
    return localized


def localize_model(model, language: str | None):
    """Wrap APIModel calls at the worker boundary so all stages share routing."""
    if normalize_prompt_language(language) != "zh" or getattr(model, "_tianluo_prompt_language", None) == "zh":
        return model
    original_generate = model.generate
    original_generate_chat = model.generate_chat

    @wraps(original_generate)
    def generate(query, *args, **kwargs):
        return original_generate(localize_text(query, "zh"), *args, **kwargs)

    @wraps(original_generate_chat)
    def generate_chat(messages, *args, **kwargs):
        return original_generate_chat(localize_messages(messages, "zh"), *args, **kwargs)

    model.generate = generate
    model.generate_chat = generate_chat
    model._tianluo_prompt_language = "zh"
    return model
