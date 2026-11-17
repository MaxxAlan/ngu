"""llama.cpp adapter must send the same request and surface the same text as the old client."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.infrastructure.llm import client as legacy
from app.models.backends.llama_cpp import LlamaCppBackend

FIXTURE = {
    "id": "chatcmpl-parity",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "parity-text",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": "{\"path\":\"a.py\"}",
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
}


class _Resp:
    status_code = 200
    text = ""

    def json(self):
        return json.loads(json.dumps(FIXTURE))


class _AsyncClient:
    captured: list[bytes] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, **kwargs):
        body = json_mod.dumps(json, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        _AsyncClient.captured.append(body.encode("utf-8"))
        return _Resp()


json_mod = json


@pytest.fixture()
def patched_llama(monkeypatch):
    _AsyncClient.captured = []

    async def _base(_model):
        return "http://127.0.0.1:9"

    monkeypatch.setattr("app.infrastructure.llm.client.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr("app.infrastructure.llm.client.runtime.resolve_base_url", _base)
    return _AsyncClient.captured


def test_adapter_request_and_text_match_legacy_client(patched_llama):
    messages = [{"role": "user", "content": "read a.py"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "read",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]
    old = asyncio.run(
        legacy.chat(
            "qwen2.5-coder-3b",
            messages,
            tools=tools,
            temperature=0.2,
            max_tokens=128,
        )
    )
    new = LlamaCppBackend(model="qwen2.5-coder-3b").generate(
        messages,
        tools,
        model="qwen2.5-coder-3b",
        temperature=0.2,
        max_tokens=128,
    )

    assert len(patched_llama) == 2
    assert patched_llama[0] == patched_llama[1]
    old_text = old["choices"][0]["message"]["content"]
    assert new.text == old_text
    assert new.text == "parity-text"
    old_args = old["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert new.tool_calls[0]["name"] == "read_file"
    assert new.tool_calls[0]["arguments"] == json.loads(old_args)
    assert new.usage["prompt_tokens"] == old["usage"]["prompt_tokens"]
    assert new.usage["completion_tokens"] == old["usage"]["completion_tokens"]
    assert new.finish_reason == old["choices"][0]["finish_reason"]
    # The adapter kept the original tool-call objects for history replay.
    assert new.raw_tool_calls == old["choices"][0]["message"]["tool_calls"]
