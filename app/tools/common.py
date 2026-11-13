from __future__ import annotations

import sys
from pathlib import Path

from .. import config

WORKSPACE = config.WORKSPACE_DIR


class ToolError(RuntimeError):
    pass


class PermissionDenied(ToolError):
    pass


def set_workspace(path: str | Path) -> None:
    global WORKSPACE
    WORKSPACE = Path(path).resolve()
    config.WORKSPACE_DIR = WORKSPACE


def _in_workspace(p: Path) -> bool:
    try:
        p.resolve().relative_to(WORKSPACE.resolve())
        return True
    except ValueError:
        return False


def _ask_permission(action: str, target: str) -> bool:
    if config.AGENT_ALLOW_ABS:
        return True
    if not config.ASK_PERMISSION:
        return False
    if not (sys.stdin and sys.stdin.isatty()):
        return False
    prompt = (
        f"\n[permission] {action}: {target}\n"
        f"  outside workspace ({WORKSPACE})\n"
        "  allow? [y]es / [n]o / [a]lways(auto): "
    )
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if ans in ("a", "always", "auto"):
        config.AGENT_ALLOW_ABS = True
        return True
    return ans in ("y", "yes")


def resolve_path(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = WORKSPACE / p
    p = p.resolve()
    if not _in_workspace(p):
        if not _ask_permission("access path", str(p)):
            raise PermissionDenied(
                f"path '{path}' is outside the workspace ({WORKSPACE}). "
                "Denied by user. Use /auto or set LA_AGENT_ALLOW_ABS=1 to allow."
            )
    return p


def clip(text: str, limit: int | None = None) -> str:
    limit = limit or config.AGENT_MAX_OUTPUT
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def rel_or_abs(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(WORKSPACE.resolve()))
    except Exception:  # noqa: BLE001
        return str(p)
