"""LLM gateway.

Completions go through the ``ModelBackend`` selected by ``app.router``.
Catalog / health helpers stay on the llama.cpp client so ``ngu models`` and
the board keep working when the generation backend is remote.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.infrastructure.llm.client import LLMError, health, list_models
from app.models.backend import ModelResponse
from app.router import acomplete

__all__ = [
    "LLMError",
    "chat",
    "health",
    "list_models",
    "stream_chat",
    "stream_chat_events",
    "to_openai_dict",
]


def to_openai_dict(resp: ModelResponse) -> dict:
    """Shape novel/vision callers still read (``choices[0].message``)."""
    tool_calls = resp.raw_tool_calls
    if not tool_calls and resp.tool_calls:
        tool_calls = []
        for i, call in enumerate(resp.tool_calls):
            args = call.get("arguments", {})
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            tool_calls.append(
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": call.get("name") or "", "arguments": args},
                }
            )
    message: dict[str, Any] = {"role": "assistant", "content": resp.text or ""}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": resp.finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": int((resp.usage or {}).get("prompt_tokens") or 0),
            "completion_tokens": int((resp.usage or {}).get("completion_tokens") or 0),
        },
    }


async def chat(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    retries: int = 1,
) -> dict:
    """Non-streaming chat. Returns an OpenAI-shaped dict built from ModelResponse."""
    resp = await acomplete(
        messages,
        tools,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        retries=retries,
    )
    return to_openai_dict(resp)


async def stream_chat_events(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    retries: int = 1,
) -> AsyncIterator[dict]:
    """One-shot events from ``ModelBackend.generate`` (content, then tool calls)."""
    resp = await acomplete(
        messages,
        tools,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        retries=retries,
    )
    if resp.text:
        yield {"content": resp.text}
    if resp.raw_tool_calls:
        for call in resp.raw_tool_calls:
            yield {"tool_call": call}
        return
    for idx, call in enumerate(resp.tool_calls):
        args = call.get("arguments", {})
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        yield {
            "tool_call": {
                "index": idx,
                "id": f"call_{idx}",
                "type": "function",
                "function": {"name": call.get("name") or "", "arguments": args},
            }
        }


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
