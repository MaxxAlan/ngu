from __future__ import annotations

import asyncio
import re
import sys

from .. import config
from .common import WORKSPACE, PermissionDenied, ToolError, _ask_permission, clip


async def run_shell(command: str, timeout: float | None = None) -> str:
    if timeout is None:
        timeout = config.AGENT_SHELL_TIMEOUT
    if not config.AGENT_ALLOW_ABS and config.ASK_PERMISSION and (sys.stdin and sys.stdin.isatty()):
        cmd = command.strip()
        outside = re.search(r'(?i)\b([a-z]:\\|\\\\)', cmd)
        mentions_parent = "..\\" in cmd or "../" in cmd
        if outside or mentions_parent:
            if not _ask_permission("run shell command", cmd[:200]):
                raise PermissionDenied("shell command denied by user. Use /auto or set LA_AGENT_ALLOW_ABS=1.")
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
