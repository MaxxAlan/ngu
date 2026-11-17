"""llama.cpp backend.

Wraps app/llama_runtime (the process manager) and the existing chat client.
The HTTP body is built by that client, so a given prompt produces the same
request — and the same completion text — as the pre-adapter path.
"""
from __future__ import annotations

from typing import Any

from app import config
from app.infrastructure.llm.client import LLMError
from app.infrastructure.llm.client import chat as legacy_chat
from app.infrastructure.llm.runtime import RuntimeUnavailable
from app.llama_runtime import runtime as llama_runtime
from app.models.backend import BackendUnreachable, ModelResponse, run_coroutine
from app.models.normalize import normalize_response


class LlamaCppBackend:
    """Local GGUF models served by llama-server. ``name`` is ``llama_cpp``."""

    name = "llama_cpp"

    def __init__(self, model: str | None = None, context_limit: int | None = None) -> None:
        self.model = (model or config.MODEL_NAME or config.DEFAULT_MODEL).strip()
        self._context_limit = int(context_limit or config.LLAMA_CTX)
        # Keep a live reference to the process manager this adapter wraps.
        self._runtime = llama_runtime

    def context_limit(self) -> int:
        return self._context_limit

    def supports_tool_calls(self) -> bool:
        return True

    def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kw: Any,
    ) -> ModelResponse:
        # Per-request model wins so the existing scout/master/vision router
        # still picks a GGUF. LA_MODEL_NAME is only the default.
        model = (kw.get("model") or self.model or config.DEFAULT_MODEL).strip()
        try:
            raw = run_coroutine(
                legacy_chat(
                    model,
                    messages,
                    tools=tools,
                    temperature=kw.get("temperature"),
                    max_tokens=kw.get("max_tokens"),
                    retries=int(kw.get("retries", 1)),
                )
            )
        except RuntimeUnavailable as exc:
            raise BackendUnreachable(str(exc)) from exc
        except LLMError as exc:
            low = str(exc).lower()
            if "unreachable" in low or "connection" in low:
                raise BackendUnreachable(str(exc)) from exc
            raise
        if not isinstance(raw, dict):
            raise BackendUnreachable("llama-server returned a non-object response")
        return normalize_response(raw, backend="llama_cpp")
