"""Model backend interface.

Every completion the agent makes goes through ``ModelBackend``. Concrete
implementations live in the backends package and are imported only by the
router.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from app import config


class BackendUnreachable(RuntimeError):
    """The backend could not be contacted. The router may try the next one."""


class BackendError(RuntimeError):
    """The backend answered, but rejected the request (HTTP 4xx, bad payload)."""


@dataclass
class ModelResponse:
    """Canonical completion. ``tool_calls`` items are ``{name, arguments}``."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0}
    )
    finish_reason: str = "stop"
    # Original provider tool-call objects, when the backend had them.
    # The llama.cpp adapter keeps these so the agent loop replays the same
    # history it did before the adapter existed.
    raw_tool_calls: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.text is None:
            self.text = ""
        if self.tool_calls is None:
            self.tool_calls = []
        if not isinstance(self.usage, dict):
            self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.usage.setdefault("prompt_tokens", 0)
        self.usage.setdefault("completion_tokens", 0)
        if not self.finish_reason:
            self.finish_reason = "stop"


@runtime_checkable
class ModelBackend(Protocol):
    name: str

    def context_limit(self) -> int: ...

    def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kw: Any,
    ) -> ModelResponse: ...

    def supports_tool_calls(self) -> bool: ...


_app_loop: asyncio.AbstractEventLoop | None = None


def bind_app_loop(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Remember the app loop so a worker thread can hop back onto it."""
    global _app_loop
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
    _app_loop = loop


def run_coroutine(coro: Any, timeout: float | None = None) -> Any:
    """Block until ``coro`` finishes.

    Sync ``generate()`` is called from a worker thread (see ``router.acomplete``).
    llama.cpp's process manager is bound to the app loop, so the coroutine has
    to run there. Sync tests have no app loop and just use ``asyncio.run``.
    """
    if timeout is None:
        timeout = float(config.REQUEST_TIMEOUT) + 30.0
    try:
        asyncio.get_running_loop()
        on_loop_thread = True
    except RuntimeError:
        on_loop_thread = False
    if on_loop_thread:
        raise RuntimeError("run_coroutine() cannot block the running event-loop thread")
    loop = _app_loop
    if loop is not None and loop.is_running() and not loop.is_closed():
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)
    return asyncio.run(coro)


def post_json(
    url: str,
    payload: dict,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> dict:
    """POST JSON. Connection failures and HTTP 5xx raise ``BackendUnreachable``."""
    limit = float(config.REQUEST_TIMEOUT if timeout is None else timeout)
    try:
        with httpx.Client(timeout=httpx.Timeout(limit, connect=10.0)) as client:
            res = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise BackendUnreachable(str(exc)) from exc
    if res.status_code >= 500:
        raise BackendUnreachable(f"HTTP {res.status_code}: {res.text[:400]}")
    if res.status_code >= 400:
        raise BackendError(f"HTTP {res.status_code}: {res.text[:800]}")
    try:
        data = res.json()
    except ValueError as exc:
        raise BackendError(f"backend returned non-JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BackendError("backend JSON was not an object")
    return data
