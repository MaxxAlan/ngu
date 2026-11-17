"""OpenAI-compatible backend (vLLM, LM Studio, OpenAI, llama-server ``/v1``).

Config: ``LA_MODEL_OPENAI_BASE_URL``, ``LA_MODEL_OPENAI_API_KEY``, and the model
id (``LA_MODEL_OPENAI_NAME`` or ``LA_MODEL_NAME``). ``base_url`` may be the API
root (``http://host:8000/v1``) or the full ``.../chat/completions`` URL.
No local GPU is required.
"""
from __future__ import annotations

from typing import Any

from app import config
from app.models.backend import ModelResponse, post_json
from app.models.normalize import normalize_response


def chat_completions_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = "http://127.0.0.1:8000/v1"
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


class OpenAICompatBackend:
    name = "openai_compat"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        context_limit: int | None = None,
    ) -> None:
        self.base_url = (base_url or config.MODEL_OPENAI_BASE_URL).strip()
        self.api_key = (api_key if api_key is not None else config.MODEL_OPENAI_API_KEY).strip()
        self.model = (model or config.MODEL_OPENAI_NAME or config.MODEL_NAME or "local-model").strip()
        self._context_limit = int(context_limit or config.MODEL_CONTEXT or 8192)

    def context_limit(self) -> int:
        return self._context_limit

    def supports_tool_calls(self) -> bool:
        return True

    def _model_id(self, kw: dict) -> str:
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
            "messages": messages,
            "temperature": config.TEMPERATURE if kw.get("temperature") is None else kw["temperature"],
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if kw.get("max_tokens"):
            payload["max_tokens"] = kw["max_tokens"]
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        raw = post_json(chat_completions_url(self.base_url), payload, headers=headers or None)
        return normalize_response(raw, backend="openai_compat")
