"""Ollama backend. Talks to ``POST {base}/api/chat`` (not the OpenAI shim).

Ollama tool calls look like ``{"function": {"name", "arguments": {object}}}``.
Arguments are sometimes a JSON string; ``normalize_response`` accepts both.
No GPU is required here — point ``LA_MODEL_OLLAMA_BASE_URL`` at any host.
"""
from __future__ import annotations

import json
from typing import Any

from app import config
from app.models.backend import ModelResponse, post_json
from app.models.normalize import normalize_response


def _ollama_messages(messages: list[dict]) -> list[dict]:
    """Ollama wants tool-call arguments as objects, not JSON strings."""
    out: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        item = dict(msg)
        calls = item.get("tool_calls")
        if isinstance(calls, list):
            fixed: list[dict] = []
            for call in calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else call
                name = fn.get("name") or call.get("name") or ""
                args = fn.get("arguments")
                if args is None:
                    args = call.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                if not isinstance(args, dict):
                    args = {"value": args}
                if name:
                    fixed.append({"function": {"name": name, "arguments": args}})
            item["tool_calls"] = fixed
        out.append(item)
    return out


class OllamaBackend:
    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        context_limit: int | None = None,
    ) -> None:
        self.base_url = (base_url or config.MODEL_OLLAMA_BASE_URL).rstrip("/")
        self.model = (model or config.MODEL_OLLAMA_NAME or config.MODEL_NAME or "qwen2.5-coder:3b").strip()
        self._context_limit = int(context_limit or config.MODEL_CONTEXT or 8192)

    def context_limit(self) -> int:
        return self._context_limit

    def supports_tool_calls(self) -> bool:
        return True

    def _model_id(self, kw: dict) -> str:
        # The agent passes a local GGUF alias. A remote Ollama tag is different,
        # so the configured name wins unless the caller sets model_override.
        if kw.get("model_override"):
            return str(kw["model_override"])
        return self.model or str(kw.get("model") or "")

    def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kw: Any,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self._model_id(kw),
            "messages": _ollama_messages(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        options: dict[str, Any] = {}
        temperature = kw.get("temperature")
        if temperature is None:
            temperature = config.TEMPERATURE
        if temperature is not None:
            options["temperature"] = temperature
        if kw.get("max_tokens"):
            options["num_predict"] = kw["max_tokens"]
        if options:
            payload["options"] = options
        raw = post_json(self.base_url + "/api/chat", payload)
        return normalize_response(raw, backend="ollama")
