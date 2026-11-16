"""Path resolution, workspace sandbox, and permission helpers.

Permission grants (ALLOWED_PATHS / ALLOWED_WRITE_PATHS) are scoped via contextvars
so one task cannot permanently open an external path for every later caller in the
same process. Global sets remain as a fallback for interactive CLI /auto grants
within the same process lifetime, but runner tasks use an isolated scope.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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


def _in_upload_dir(p: Path) -> bool:
    try:
        p.resolve().relative_to(config.UPLOAD_DIR.resolve())
        return True
    except ValueError:
        return False


def _ask_permission(action: str, target: str) -> bool:
    if config.AGENT_ALLOW_ABS:
        return True
    if not config.ASK_PERMISSION:
        return False
    if not (sys.stdin and sys.stdin.isatty()):
        # Non-interactive (API/MCP/server): fail-safe deny unless auto mode.
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


# Process-level grants (CLI interactive /auto within one process). Prefer scoped grants.
ALLOWED_PATHS: set[str] = set()
ALLOWED_WRITE_PATHS: set[str] = set()

# Per-task / per-request scoped grants (set by runner while a task executes).
_scoped_read: ContextVar[set[str] | None] = ContextVar("scoped_read_paths", default=None)
_scoped_write: ContextVar[set[str] | None] = ContextVar("scoped_write_paths", default=None)


@contextmanager
def path_permission_scope() -> Iterator[None]:
    """Isolate allow_path/allow_write_path grants to the current async/task context."""
    read_tok = _scoped_read.set(set())
    write_tok = _scoped_write.set(set())
    try:
        yield
    finally:
        _scoped_read.reset(read_tok)
        _scoped_write.reset(write_tok)


def allow_path(path: str | Path) -> None:
    """Allow read access to an external path for the current scope (or process if no scope)."""
    resolved = str(Path(path).expanduser().resolve().as_posix())
    scoped = _scoped_read.get()
    if scoped is not None:
        scoped.add(resolved)
    else:
        ALLOWED_PATHS.add(resolved)


def allow_write_path(path: str | Path) -> None:
    """Pre-authorize a write target for the current scope (or process if no scope)."""
    resolved = str(Path(path).expanduser().resolve().as_posix())
    scoped_w = _scoped_write.get()
    scoped_r = _scoped_read.get()
    if scoped_w is not None:
        scoped_w.add(resolved)
        if scoped_r is not None:
            scoped_r.add(resolved)
        return
    ALLOWED_WRITE_PATHS.add(resolved)
    ALLOWED_PATHS.add(resolved)


def _is_allowed(p: Path) -> bool:
    target = p.resolve().as_posix()
    pools: list[set[str]] = []
    scoped = _scoped_read.get()
    if scoped is not None:
        pools.append(scoped)
    pools.append(ALLOWED_PATHS)
    for pool in pools:
        for allowed in pool:
            if target == allowed or target.startswith(allowed + "/"):
                return True
    return False


def _is_write_allowed(p: Path) -> bool:
    target = p.resolve().as_posix()
    pools: list[set[str]] = []
    scoped = _scoped_write.get()
    if scoped is not None:
        pools.append(scoped)
    pools.append(ALLOWED_WRITE_PATHS)
    for pool in pools:
        for allowed in pool:
            if target == allowed or target.startswith(allowed + "/"):
                return True
    return False


def resolve_path(path: str, write: bool = False) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = WORKSPACE / p
    p = p.resolve()
    if not _in_workspace(p):
        if write and _is_write_allowed(p):
            return p
        if not write and _is_allowed(p):
            return p
        if not _ask_permission("modify path" if write else "access path", str(p)):
            raise PermissionDenied(
                f"path '{path}' is outside the workspace ({WORKSPACE}). "
                "Denied by user. Use /auto or set LA_AGENT_ALLOW_ABS=1 to allow."
            )
    return p


def resolve_attachment_path(path: str | Path) -> Path:
    """Resolve a task attachment path.

    Attachments may only live under UPLOAD_DIR (from /api/upload) or the workspace.
    Absolute paths outside those trees are always rejected — no interactive prompt,
    no permanent ALLOWED_PATHS grant. Used by API/MCP/runner before reading file content.
    """
    raw = str(path).strip()
    if not raw:
        raise PermissionDenied("attachment path must not be empty")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        # Relative paths are resolved against UPLOAD_DIR first (upload session files),
        # then workspace — never against arbitrary cwd outside those roots.
        cand_upload = (config.UPLOAD_DIR / p).resolve()
        if cand_upload.exists() and _in_upload_dir(cand_upload):
            return cand_upload
        p = (WORKSPACE / p).resolve()
    else:
        p = p.resolve()
    if _in_upload_dir(p) or _in_workspace(p):
        return p
    raise PermissionDenied(
        f"attachment path '{path}' is outside upload dir and workspace. "
        "Upload via /api/upload and use the returned path, or place the file in the workspace."
    )


_PATH_TOKEN_RE = re.compile(
    r"(?:"
    r"[A-Za-z]:[\\/][^\s\"'<>|]+"  # Windows drive path
    r"|\\\\[^\s\"'<>|]+"  # UNC
    r"|(?<![\\w:.\\-])/[^\s\"'<>|]+"  # Unix absolute
    r")"
)


def extract_path_tokens(command: str) -> list[str]:
    """Pull path-like tokens from a shell command (Unix + Windows)."""
    found: list[str] = []
    for m in _PATH_TOKEN_RE.finditer(command or ""):
        tok = m.group(0).strip().rstrip("\\/")
        if tok:
            found.append(tok)
    # Parent-dir traversal markers
    if "../" in command or "..\\" in command:
        found.append("..")
    return found


def command_references_outside_workspace(command: str) -> bool:
    """True if the command appears to touch paths outside the workspace."""
    for tok in extract_path_tokens(command):
        if tok in ("..",):
            return True
        # Unix-style absolute path token (also appears on Windows as "\etc\...")
        if tok.startswith("/") and not tok.startswith("//"):
            # Rooted absolute path (Unix style; also seen in cross-platform cmds).
            try:
                p = Path(tok).expanduser().resolve()
                if _in_workspace(p) or _in_upload_dir(p):
                    continue
            except (OSError, RuntimeError):
                pass
            return True
        try:
            p = Path(tok).expanduser()
            if not p.is_absolute():
                continue
            p = p.resolve()
        except (OSError, RuntimeError):
            continue
        if not _in_workspace(p) and not _in_upload_dir(p):
            return True
    return False


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
