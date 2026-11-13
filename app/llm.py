from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from . import config
from .llama_runtime import runtime
from .models import discover_models

log = logging.getLogger("local-agents.llm")


class LLMError(RuntimeError):
    pass


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(config.REQUEST_TIMEOUT, connect=10.0),
    )


async def _base_url(model: str) -> str:
    return await runtime.resolve_base_url(model)


async def list_models(running: bool = False) -> list[dict]:
    out = []
    for m in discover_models():
        out.append(
            {
                "name": m.name,
                "path": str(m.path),
                "size": m.size,
                "kind": m.kind,
                "parameter_size": m.parameter_size,
                "quantization": m.quantization,
                "vision": m.kind == "vision",
                "running": m.name in runtime.running() if running else False,
            }
        )
    return sorted(out, key=lambda m: m["name"])


async def health() -> dict:
    device = await runtime.device()
    return {
        "ok": config.LLAMA_SERVER.exists(),
        "llama_dir": str(config.LLAMA_DIR),
        "server": str(config.LLAMA_SERVER),
        "device": device,
        "ctx": config.LLAMA_CTX,
        "ngl": config.LLAMA_NGL,
        "models": [m.name for m in discover_models()],
        "running": runtime.running(),
    }


async def chat(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    retries: int = 1,
) -> dict:
    """Non-streaming chat completion. Returns the raw response dict.

    Restarts the llama-server once if the connection died (server crash, swap...).
    """
    base = await _base_url(model)
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": config.TEMPERATURE if temperature is None else temperature,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    if max_tokens:
        payload["max_tokens"] = max_tokens
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with _client() as client:
                res = await client.post(base + "/v1/chat/completions", json=payload)
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt >= retries:
                raise
            log.warning("llm: connection lost (%s), restarting llama-server", exc)
            try:
                await runtime.stop(model)
            except Exception:  # noqa: BLE001 - best effort
                pass
            base = await _base_url(model)
            continue
        if res.status_code >= 400:
            raise LLMError(f"llama-server HTTP {res.status_code}: {res.text[:800]}")
        return res.json()
    raise LLMError(f"llama-server unreachable: {last_exc}")


async def stream_chat_events(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[dict]:
    """Stream deltas as {"content": str} or {"tool_call": <openai delta>} events."""
    base = await _base_url(model)
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": config.TEMPERATURE if temperature is None else temperature,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
    if max_tokens:
        payload["max_tokens"] = max_tokens
    async with _client() as client:
        async with client.stream("POST", base + "/v1/chat/completions", json=payload) as res:
            if res.status_code >= 400:
                body = (await res.aread()).decode("utf-8", "replace")[:800]
                raise LLMError(f"llama-server HTTP {res.status_code}: {body}")
            async for line in res.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield {"content": piece}
                for call in delta.get("tool_calls") or []:
                    yield {"tool_call": call}


async def stream_chat(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    async for event in stream_chat_events(
        model, messages, tools=tools, temperature=temperature, max_tokens=max_tokens
    ):
        piece = event.get("content")
        if piece:
            yield piece
