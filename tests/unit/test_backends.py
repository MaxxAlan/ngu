"""Normalizer fixtures for llama.cpp, Ollama, and OpenAI-compatible backends."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from app import config
from app.models.normalize import normalize_response

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"

# Captured (trimmed) payloads. Arguments are a JSON string, an object, or absent.
LLAMA_CPP_FIXTURE = {
    "id": "chatcmpl-local",
    "object": "chat.completion",
    "model": "qwen2.5-coder-3b",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "hello from llama",
                "tool_calls": [
                    {
                        "id": "call_llama_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": "{\"path\":\"src/app.py\"}",
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 42, "completion_tokens": 17},
}

OLLAMA_OBJECT_ARGS = {
    "model": "qwen2.5-coder:3b",
    "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "list_dir",
                    "arguments": {"path": ".", "recursive": False},
                }
            }
        ],
    },
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 30,
    "eval_count": 12,
}

OLLAMA_STRING_ARGS = {
    "model": "qwen2.5-coder:3b",
    "message": {
        "role": "assistant",
        "content": "looking",
        "tool_calls": json.dumps(
            [
                {
                    "function": {
                        "name": "search_files",
                        "arguments": "{\"query\":\"TODO\"}",
                    }
                }
            ]
        ),
    },
    "done_reason": "stop",
    "prompt_eval_count": 8,
    "eval_count": 4,
}

OLLAMA_PLAIN = {
    "model": "qwen2.5-coder:3b",
    "message": {"role": "assistant", "content": "Xin chao"},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 5,
    "eval_count": 3,
}

OPENAI_STRING_ARGS = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_oa",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": "{\"cmd\":\"pytest -q\"}",
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
}

OPENAI_OBJECT_ARGS = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "done",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "memory_save", "arguments": {"text": "note"}},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 9},
}

OPENAI_NO_TOOLS = {
    "choices": [
        {
            "message": {"role": "assistant", "content": "plain answer"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 2, "completion_tokens": 2},
}


def test_normalize_llama_cpp_json_string_arguments():
    resp = normalize_response(LLAMA_CPP_FIXTURE, backend="llama_cpp")
    assert resp.text == "hello from llama"
    assert resp.finish_reason == "tool_calls"
    assert resp.usage == {"prompt_tokens": 42, "completion_tokens": 17}
    assert resp.tool_calls == [
        {"name": "read_file", "arguments": {"path": "src/app.py"}},
    ]
    assert resp.raw_tool_calls == LLAMA_CPP_FIXTURE["choices"][0]["message"]["tool_calls"]


def test_normalize_ollama_object_arguments():
    resp = normalize_response(OLLAMA_OBJECT_ARGS, backend="ollama")
    assert resp.text == ""
    assert resp.tool_calls == [
        {"name": "list_dir", "arguments": {"path": ".", "recursive": False}},
    ]
    assert resp.usage == {"prompt_tokens": 30, "completion_tokens": 12}
    assert resp.finish_reason == "stop"
    assert resp.raw_tool_calls is None


def test_normalize_ollama_tool_calls_json_string():
    resp = normalize_response(OLLAMA_STRING_ARGS, backend="ollama")
    assert resp.text == "looking"
    assert resp.tool_calls == [
        {"name": "search_files", "arguments": {"query": "TODO"}},
    ]


def test_normalize_ollama_no_tool_calls():
    resp = normalize_response(OLLAMA_PLAIN, backend="ollama")
    assert resp.text == "Xin chao"
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"
    assert resp.usage["prompt_tokens"] == 5


def test_normalize_openai_string_arguments():
    resp = normalize_response(OPENAI_STRING_ARGS, backend="openai_compat")
    assert resp.text == ""
    assert resp.tool_calls == [{"name": "run_shell", "arguments": {"cmd": "pytest -q"}}]
    assert resp.finish_reason == "tool_calls"
    assert resp.usage == {"prompt_tokens": 100, "completion_tokens": 20}


def test_normalize_openai_object_arguments():
    resp = normalize_response(OPENAI_OBJECT_ARGS, backend="openai_compat")
    assert resp.text == "done"
    assert resp.tool_calls[0]["name"] == "memory_save"
    assert resp.tool_calls[0]["arguments"] == {"text": "note"}


def test_normalize_openai_no_tool_calls():
    resp = normalize_response(OPENAI_NO_TOOLS, backend="openai_compat")
    assert resp.text == "plain answer"
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"


def test_normalize_broken_argument_string_is_raw():
    raw = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "read_file", "arguments": "{not json"}}
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    resp = normalize_response(raw, backend="llama_cpp")
    assert resp.tool_calls == [{"name": "read_file", "arguments": {"_raw": "{not json"}}]


def test_only_router_imports_backend_implementations():
    needle = "app.models.backends"
    hits = []
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if needle in text:
            hits.append(path.relative_to(APP).as_posix())
    assert hits == ["router.py"]


def test_llama_runtime_string_only_in_adapter_and_router():
    needle = "app/llama_runtime"
    hits = []
    for path in APP.rglob("*.py"):
        if needle in path.read_text(encoding="utf-8"):
            hits.append(path.relative_to(APP).as_posix())
    assert sorted(hits) == ["models/backends/llama_cpp.py", "router.py"]


class _ScriptedClient:
    """Sync httpx stand-in. Fails Ollama, serves OpenAI."""

    def __init__(self, *args, **kwargs):
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        _ScriptedClient.urls.append(url)
        if url.endswith("/api/chat"):
            raise httpx.ConnectError("ollama down")
        body = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "from-openai"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return httpx.Response(200, json=body)


_ScriptedClient.urls = []


def test_fallback_chain_when_primary_unreachable(monkeypatch, caplog):
    monkeypatch.setattr(config, "MODEL_BACKEND", "ollama")
    monkeypatch.setattr(config, "MODEL_FALLBACK_CHAIN", "openai_compat")
    monkeypatch.setattr(config, "MODEL_NAME", "qwen2.5-coder:3b")
    monkeypatch.setattr(config, "MODEL_OLLAMA_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(config, "MODEL_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(config, "MODEL_OPENAI_API_KEY", "")
    monkeypatch.setattr("app.models.backend.httpx.Client", _ScriptedClient)
    _ScriptedClient.urls = []

    from app.router import get_backend

    caplog.set_level(logging.WARNING, logger="local-agents.router")
    backend = get_backend()
    resp = backend.generate([{"role": "user", "content": "ping"}])
    assert resp.text == "from-openai"
    assert resp.finish_reason == "stop"
    assert any(url.endswith("/api/chat") for url in _ScriptedClient.urls)
    assert any(url.endswith("/chat/completions") for url in _ScriptedClient.urls)
    assert "falling back to openai_compat" in caplog.text


def test_default_backend_is_llama_cpp(monkeypatch):
    monkeypatch.setattr(config, "MODEL_BACKEND", "llama_cpp")
    monkeypatch.setattr(config, "MODEL_FALLBACK_CHAIN", "")
    from app.router import get_backend

    backend = get_backend()
    assert backend.name == "llama_cpp"
    assert backend.supports_tool_calls() is True
    assert backend.context_limit() == config.LLAMA_CTX


@pytest.mark.asyncio
async def test_same_benchmark_prompt_completes_on_llama_and_ollama(monkeypatch):
    """Both backends must finish the same catalog prompt (quality may differ)."""
    catalog = json.loads((ROOT / "tests" / "benchmarks" / "catalog.json").read_text(encoding="utf-8"))
    prompt = catalog["tasks"][0]["prompt"]
    messages = [{"role": "user", "content": prompt}]

    llama_payload = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "llama-ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 1},
    }

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            return _Resp(llama_payload)

    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self.text = ""
            self._payload = payload

        def json(self):
            return self._payload

    async def _base(_model):
        return "http://127.0.0.1:9"

    monkeypatch.setattr("app.infrastructure.llm.client.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr("app.infrastructure.llm.client.runtime.resolve_base_url", _base)
    monkeypatch.setattr(config, "MODEL_BACKEND", "llama_cpp")
    monkeypatch.setattr(config, "MODEL_FALLBACK_CHAIN", "")

    from app.router import acomplete

    llama_resp = await acomplete(messages, None, model="qwen2.5-coder-3b", max_tokens=32)
    assert llama_resp.text == "llama-ok"
    assert llama_resp.finish_reason == "stop"

    class _OllamaClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):
            assert url.endswith("/api/chat")
            assert json["messages"][0]["content"] == prompt
            return httpx.Response(
                200,
                json={
                    "message": {"role": "assistant", "content": "ollama-ok"},
                    "done_reason": "stop",
                    "prompt_eval_count": 4,
                    "eval_count": 1,
                },
            )

    monkeypatch.setattr(config, "MODEL_BACKEND", "ollama")
    monkeypatch.setattr(config, "MODEL_OLLAMA_NAME", "qwen2.5-coder:3b")
    monkeypatch.setattr("app.models.backend.httpx.Client", _OllamaClient)
    ollama_resp = await acomplete(messages, None, model="qwen2.5-coder-3b", max_tokens=32)
    assert ollama_resp.text == "ollama-ok"
    assert ollama_resp.usage["completion_tokens"] == 1
