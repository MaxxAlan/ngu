"""Shell tool with fail-safe policy for non-interactive (API/MCP) callers.

Policy:
- Interactive CLI (stdin is a TTY): keep existing ask-permission flow for paths
  outside the workspace; /auto and LA_AGENT_ALLOW_ABS still work.
- Non-interactive (server, API, MCP — no TTY): only run commands that stay
  inside the workspace/upload dir, unless AGENT_ALLOW_ABS (auto mode) is on.
  This matches resolve_path() fail-safe behaviour used by filesystem tools.
"""
from __future__ import annotations

import asyncio
import sys

from .. import config
from .common import (
    WORKSPACE,
    PermissionDenied,
    ToolError,
    _ask_permission,
    clip,
    command_references_outside_workspace,
)


async def run_shell(command: str, timeout: float | None = None) -> str:
    if timeout is None:
        timeout = config.AGENT_SHELL_TIMEOUT
    cmd = (command or "").strip()
    if not cmd:
        raise ToolError("command must not be empty")

    outside = command_references_outside_workspace(cmd)
    interactive = bool(sys.stdin and sys.stdin.isatty())

    if outside:
        if config.AGENT_ALLOW_ABS:
            pass  # auto mode: allow
        elif interactive and config.ASK_PERMISSION:
            if not _ask_permission("run shell command", cmd[:200]):
                raise PermissionDenied(
                    "shell command denied by user. Use /auto or set LA_AGENT_ALLOW_ABS=1."
                )
        else:
            # Non-interactive fail-safe (API/MCP/server without auto mode).
            raise PermissionDenied(
                "shell command references paths outside the workspace and no interactive "
                "permission prompt is available. Set LA_AGENT_ALLOW_ABS=1 for auto mode, "
                "or run from the interactive CLI and approve the prompt."
            )

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(WORKSPACE),
        )
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"cannot run command: {exc}") from exc
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=float(timeout))
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ToolError(f"command timed out after {timeout}s: {command}") from exc
    code = proc.returncode
    text = out.decode("utf-8", "replace")
    return clip(f"$ {command}\n[exit {code}]\n{text}")
