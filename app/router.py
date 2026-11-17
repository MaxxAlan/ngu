from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from app import llama_runtime  # process manager lives at app/llama_runtime
from app.models.backend import BackendUnreachable, ModelResponse, bind_app_loop
from app.models.backends.llama_cpp import LlamaCppBackend
from app.models.backends.ollama import OllamaBackend
from app.models.backends.openai_compat import OpenAICompatBackend

from . import config
from .models import discover_models, get_model

log = logging.getLogger("local-agents.router")

# Heuristics for heavy coding and complex reasoning tasks
HEAVY_KEYWORDS = {
    "refactor", "architect", "architecture", "algorithm", "benchmark",
    "reverse", "decompile", "optimize", "concurrency", "asyncio",
    "pipeline", "framework", "xay dung", "kien truc", "thuat toan",
    "nang cap", "toi uu", "chuyen doi", "phan tich sau", "viet toan bo",
    "debug sau", "kho qua", "logic phuc tap", "he thong", "seb", "sep",
}

LIGHT_KEYWORDS = {
    "chao", "hello", "hi", "helo", "test", "la ai", "ban ten gi", "dich",
    "translate", "format", "json dep", "tom tat", "giai thich ngan",
    "ping", "check", "may gio", "thoi tiet",
}


@dataclass
class ModelRole:
    worker_scout: str      # "Con ga" lam tay sai, trinh sat & viec nhe
    master_coder: str      # "Thang ngon" xu ly logic nang, code kho
    vision_specialist: str # Model co mat doc anh


def get_available_roles() -> ModelRole:
    """Identify the best models available on local machine for each role."""
    models = {m.name: m for m in discover_models()}

    # Scout / Light worker: prefer 1.5b then 3b
    scout = "qwen2.5-coder-3b"
    if "qwen2.5-coder-1.5b" in models:
        scout = "qwen2.5-coder-1.5b"
    elif "qwen2.5-coder-3b" in models:
        scout = "qwen2.5-coder-3b"
    elif models:
        scout = list(models.keys())[0]

    # Master coder / Heavy lifter: prefer 7b-coder uncensored, 7b, or gemma
    master = scout
    candidates = [
        "qwen2.5-coder-7b",
        "qwen2.5-7b",
        "nemotron3-nano-4b-uncensored-hauhaucs-aggressive-q4_k_p",
        "gemma-4-e4b",
        "qwen2.5-coder-3b",
    ]
    for c in candidates:
        if c in models:
            master = c
            break

    # Vision specialist
    vision = config.DEFAULT_VISION_MODEL
    if vision not in models:
        for name, m in models.items():
            if m.kind == "vision":
                vision = name
                break

    return ModelRole(worker_scout=scout, master_coder=master, vision_specialist=vision)


def route_model(prompt: str, has_images: bool = False, current_model: str | None = None) -> str:
    """Automatically route the request to the optimal model based on workload."""
    roles = get_available_roles()

    if has_images:
        log.info("AutoRouter -> Vision specialist: %s", roles.vision_specialist)
        return roles.vision_specialist

    low = (prompt or "").lower()
    set(re.findall(r"\w+", low))

    # Explicit light task
    if any(k in low for k in LIGHT_KEYWORDS) and len(low) < 120 and not any(k in low for k in HEAVY_KEYWORDS):
        log.info("AutoRouter -> Light Worker ('con ga' viec nhe): %s", roles.worker_scout)
        return roles.worker_scout

    # Heavy task detection
    is_heavy = (
        any(k in low for k in HEAVY_KEYWORDS)
        or len(low) > 300
        or "```" in prompt
        or "D:\\project" in prompt
        or "hepii" in low
        or "seb" in low
        or "giai thich code" in low
    )

    if is_heavy:
        log.info("AutoRouter -> Heavy Master ('thang ngon' viec nang): %s", roles.master_coder)
        return roles.master_coder

    # If already set and valid, keep it; else default to master coder
    if current_model and current_model != "auto" and get_model(current_model):
        return current_model

    return roles.master_coder


def llama_servers_running() -> list[str]:
    """Names of llama-server processes. Uses app/llama_runtime directly."""
    return list(llama_runtime.runtime.running())


def _make_backend(kind: str):
    """Construct one backend. Only this module imports ``app.models.backends``."""
    key = (kind or "").strip().lower()
    ctx = config.MODEL_CONTEXT or None
    if key == "llama_cpp":
        return LlamaCppBackend(model=config.MODEL_NAME or config.DEFAULT_MODEL, context_limit=ctx)
    if key == "ollama":
        name = config.MODEL_OLLAMA_NAME or config.MODEL_NAME or "qwen2.5-coder:3b"
        return OllamaBackend(
            base_url=config.MODEL_OLLAMA_BASE_URL,
            model=name,
            context_limit=ctx or 8192,
        )
    if key == "openai_compat":
        name = config.MODEL_OPENAI_NAME or config.MODEL_NAME or "local-model"
        return OpenAICompatBackend(
            base_url=config.MODEL_OPENAI_BASE_URL,
            api_key=config.MODEL_OPENAI_API_KEY,
            model=name,
            context_limit=ctx or 8192,
        )
    raise ValueError(f"unknown model backend '{kind}' (expected llama_cpp|ollama|openai_compat)")


def _backend_names() -> list[str]:
    primary = (config.MODEL_BACKEND or "llama_cpp").strip() or "llama_cpp"
    names: list[str] = []
    for part in [primary, *(config.MODEL_FALLBACK_CHAIN or "").split(",")]:
        part = part.strip()
        if part and part not in names:
            names.append(part)
    return names or ["llama_cpp"]


class _FallbackBackend:
    """Try each backend in order. Unreachable ones are logged and skipped."""

    def __init__(self, backends: list) -> None:
        if not backends:
            raise BackendUnreachable("no model backends configured")
        self._backends = backends
        self.name = backends[0].name

    def context_limit(self) -> int:
        return int(self._backends[0].context_limit())

    def supports_tool_calls(self) -> bool:
        return any(b.supports_tool_calls() for b in self._backends)

    def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kw: Any,
    ) -> ModelResponse:
        last: BackendUnreachable | None = None
        for idx, backend in enumerate(self._backends):
            try:
                return backend.generate(messages, tools, **kw)
            except BackendUnreachable as exc:
                last = exc
                if idx + 1 >= len(self._backends):
                    log.error(
                        "model backend %s unreachable (%s); no further fallback",
                        backend.name,
                        exc,
                    )
                    break
                nxt = self._backends[idx + 1]
                log.warning(
                    "model backend %s unreachable (%s); falling back to %s",
                    backend.name,
                    exc,
                    nxt.name,
                )
        assert last is not None
        raise last


def get_backend():
    """Backend for ``LA_MODEL_BACKEND``, wrapped in the fallback chain."""
    built = []
    for name in _backend_names():
        try:
            built.append(_make_backend(name))
        except ValueError as exc:
            log.error("skipping model backend %s: %s", name, exc)
    if not built:
        log.error("no usable model backend; falling back to llama_cpp")
        built.append(LlamaCppBackend())
    if len(built) == 1:
        return built[0]
    return _FallbackBackend(built)


def _call_generate(backend, messages: list[dict], tools: list[dict] | None, kw: dict) -> ModelResponse:
    return backend.generate(messages, tools, **kw)


async def acomplete(
    messages: list[dict],
    tools: list[dict] | None = None,
    **kw: Any,
) -> ModelResponse:
    """Async entry used by ``app.llm`` and ``app.agent``. Sync ``generate`` runs off-loop."""
    bind_app_loop()
    backend = get_backend()
    return await asyncio.to_thread(_call_generate, backend, messages, tools, kw)

