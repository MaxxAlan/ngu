"""Turn any backend payload into a canonical ``ModelResponse``.

Backends disagree about tool calls: OpenAI sends a JSON *string* in
``function.arguments``, Ollama sends a structured object (sometimes a string,
sometimes the whole ``tool_calls`` list as a string), and some replies have no
tool calls at all. Callers only see ``{name, arguments}`` plus token usage.
"""
from __future__ import annotations

import json
from typing import Any

from app.models.backend import ModelResponse


def coerce_arguments(value: Any) -> dict:
    """Arguments may arrive as an object, a JSON string, or nothing."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": value}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {"value": value}


def coerce_tool_call(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    fn = raw.get("function")
    if not isinstance(fn, dict):
        fn = raw
    name = fn.get("name") or raw.get("name") or ""
    if not isinstance(name, str) or not name.strip():
        return None
    args = fn.get("arguments")
    if args is None:
        args = raw.get("arguments", raw.get("args", {}))
    return {"name": name.strip(), "arguments": coerce_arguments(args)}


def tool_call_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        norm = coerce_tool_call(item)
        if norm:
            out.append(norm)
    return out


def _usage_pair(prompt: Any, completion: Any) -> dict[str, int]:
    def _n(v: Any) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    return {"prompt_tokens": _n(prompt), "completion_tokens": _n(completion)}


def from_openai_chat(raw: dict) -> ModelResponse:
    """llama.cpp ``/v1/chat/completions`` and any OpenAI-compatible server."""
    choices = raw.get("choices") or []
    choice = choices[0] if isinstance(choices, list) and choices else {}
    if not isinstance(choice, dict):
        choice = {}
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    text = message.get("content") or ""
    if not isinstance(text, str):
        text = str(text)
    raw_calls = message.get("tool_calls")
    calls = tool_call_list(raw_calls)
    finish = choice.get("finish_reason") or ("tool_calls" if calls else "stop")
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    preserved = raw_calls if isinstance(raw_calls, list) else None
    return ModelResponse(
        text=text,
        tool_calls=calls,
        usage=_usage_pair(usage.get("prompt_tokens"), usage.get("completion_tokens")),
        finish_reason=str(finish),
        raw_tool_calls=preserved,
    )


def from_ollama_chat(raw: dict) -> ModelResponse:
    """Ollama ``/api/chat`` (non-streaming). Tool arguments are usually objects."""
    message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
    text = message.get("content") or ""
    if not isinstance(text, str):
        text = str(text) if text else ""
    calls = tool_call_list(message.get("tool_calls"))
    if not calls:
        calls = tool_call_list(raw.get("tool_calls"))
    finish = raw.get("done_reason") or ("tool_calls" if calls else "stop")
    return ModelResponse(
        text=text,
        tool_calls=calls,
        usage=_usage_pair(raw.get("prompt_eval_count"), raw.get("eval_count")),
        finish_reason=str(finish),
        raw_tool_calls=None,
    )


def normalize_response(raw: dict, *, backend: str) -> ModelResponse:
    kind = (backend or "").strip().lower()
    if kind == "ollama":
        return from_ollama_chat(raw if isinstance(raw, dict) else {})
    return from_openai_chat(raw if isinstance(raw, dict) else {})
