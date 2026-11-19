"""Optional LLM backend for llm_judge criteria.

Uses LA_VERIFIER_MODEL when set; otherwise the same model as the doer.
Always runs under the skeptical auditor system prompt.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app import config
from app.core.verification.verifier import VERIFIER_SYSTEM_PROMPT

_log = logging.getLogger("local-agents.verifier.llm")


def judge(prompt: str, *, model: str | None = None) -> tuple[bool, str]:
    """Return (passed, reasoning). Never raises — failures become passed=False."""
    model_name = (model or "").strip() or (getattr(config, "VERIFIER_MODEL", "") or "").strip()
    # Empty VERIFIER_MODEL → same as doer (router picks DEFAULT_MODEL).

    messages = [
        {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    try:
        # Prefer synchronous path if available; fall back to asyncio.run.
        text = _complete_sync(messages, model_name=model_name or None)
    except Exception as exc:  # noqa: BLE001
        _log.warning("verifier llm call failed: %s", exc)
        return False, f"llm call failed: {exc}"

    return _parse_judge_response(text)


def _complete_sync(messages: list[dict[str, Any]], *, model_name: str | None) -> str:
    """Best-effort sync completion against the configured router/backend."""
    try:
        from app.router import complete

        kwargs: dict[str, Any] = {"messages": messages, "max_tokens": 512, "temperature": 0.0}
        if model_name:
            kwargs["model"] = model_name
        out = complete(**kwargs)
        if isinstance(out, dict):
            return str(out.get("content") or out.get("text") or out)
        return str(out)
    except Exception:
        # Async-only router — try acomplete via asyncio.
        import asyncio

        from app.router import acomplete

        async def _run() -> str:
            kwargs: dict[str, Any] = {
                "messages": messages,
                "max_tokens": 512,
                "temperature": 0.0,
            }
            if model_name:
                kwargs["model"] = model_name
            out = await acomplete(**kwargs)
            if isinstance(out, dict):
                return str(out.get("content") or out.get("text") or out)
            return str(out)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # Nested event loop — run in a thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(_run())).result(timeout=120)
        return asyncio.run(_run())


def _parse_judge_response(text: str) -> tuple[bool, str]:
    text = (text or "").strip()
    # Prefer JSON object in the response.
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            passed = bool(obj.get("passed"))
            reasoning = str(obj.get("reasoning") or obj.get("reason") or text)
            return passed, reasoning
        except (json.JSONDecodeError, TypeError):
            pass
    # Heuristic fallback.
    low = text.lower()
    if "passed\": true" in low or "\"passed\": true" in low or low.startswith("pass"):
        return True, text
    if "fail" in low or "incomplete" in low or "incorrect" in low:
        return False, text
    # Default skeptical: no clear pass → fail.
    return False, text or "empty judge response"
