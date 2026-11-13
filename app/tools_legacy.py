from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Any

from . import config

WORKSPACE = config.WORKSPACE_DIR


class ToolError(RuntimeError):
    pass


class PermissionDenied(ToolError):
    pass


def set_workspace(path: str | Path) -> None:
    global WORKSPACE
    WORKSPACE = Path(path).resolve()
    config.WORKSPACE_DIR = WORKSPACE


TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file. Returns up to `limit` lines starting at `offset` (0-based).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the workspace or absolute."},
                    "offset": {"type": "integer", "description": "First line to read (0-based).", "default": 0},
                    "limit": {"type": "integer", "description": "Max lines to return.", "default": 2000},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace `old_text` with `new_text` in a file. Fails if old_text is not found or is ambiguous.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories under `path` (non-recursive).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Recursively search file contents with a regular expression. Returns matching lines with file and line number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression."},
                    "path": {"type": "string", "description": "Directory or file to search.", "default": "."},
                    "glob": {"type": "string", "description": "File glob to include, e.g. '*.py'.", "default": "*"},
                    "max_results": {"type": "integer", "default": 50},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command and return stdout+stderr. Use for builds, tests, git, and scripts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "number", "description": "Seconds.", "default": 60},
                },
                "required": ["command"],
            },
        },
    },
]


def _in_workspace(p: Path) -> bool:
    try:
        p.resolve().relative_to(WORKSPACE.resolve())
        return True
    except ValueError:
        return False


def _ask_permission(action: str, target: str) -> bool:
    """Ask user to allow an out-of-workspace action. Returns True if allowed."""
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


def _clip(text: str, limit: int | None = None) -> str:
    limit = limit or config.AGENT_MAX_OUTPUT
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def _read(path: Path, offset: int, limit: int) -> str:
    if not path.exists():
        raise ToolError(f"no such file: {path}")
    if path.is_dir():
        raise ToolError(f"{path} is a directory")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"cannot read {path}: {exc}") from exc
    lines = text.splitlines()
    total = len(lines)
    chunk = lines[offset : offset + limit]
    body = "\n".join(f"{i + offset}: {line}" for i, line in enumerate(chunk))
    return f"{path} ({total} lines)\n{body}"


def _write(path: Path, content: str) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"cannot write {path}: {exc}") from exc
    return f"wrote {len(content)} chars to {path}"


def _edit(path: Path, old: str, new: str, replace_all: bool) -> str:
    if not path.exists():
        raise ToolError(f"no such file: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    count = text.count(old)
    if count == 0:
        raise ToolError(f"old_text not found in {path}")
    if count > 1 and not replace_all:
        raise ToolError(f"old_text found {count} times in {path}; use replace_all=true or more context")
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    path.write_text(updated, encoding="utf-8")
    return f"edited {path} ({count} occurrence(s))"


def _list_dir(path: Path) -> str:
    if not path.exists():
        raise ToolError(f"no such directory: {path}")
    if path.is_file():
        raise ToolError(f"{path} is a file, not a directory")
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    lines = []
    for e in entries[:500]:
        try:
            is_dir = e.is_dir()
            kind = "dir" if is_dir else "file"
            size = "" if is_dir else f" ({e.stat().st_size} B)"
        except OSError:
            kind, size = "?", ""
        lines.append(f"{kind:4} {e.name}{size}")
    more = "" if len(entries) <= 500 else f"\n... {len(entries) - 500} more"
    return _clip(f"{path} ({len(entries)} entries)\n" + "\n".join(lines) + more)


def _search(pattern: str, path: Path, glob: str, max_results: int) -> str:
    if not path.exists():
        raise ToolError(f"no such path: {path}")
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"bad regex: {exc}") from exc
    files = [path] if path.is_file() else list(path.rglob(glob))
    hits: list[str] = []
    for f in files:
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            rel = f.relative_to(WORKSPACE)
        except ValueError:
            rel = f
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{i}: {line.strip()}")
                if len(hits) >= max_results:
                    return _clip("\n".join(hits) + "\n[result limit reached]")
    if not hits:
        return f"no matches for /{pattern}/ under {path}"
    return _clip("\n".join(hits))


async def _run_shell(command: str, timeout: float) -> str:
    # Best-effort prompt for clearly out-of-workspace commands when not in auto mode.
    if not config.AGENT_ALLOW_ABS and config.ASK_PERMISSION and (sys.stdin and sys.stdin.isatty()):
        cmd = command.strip()
        outside = re.search(r'(?i)\b([a-z]:\\|\\\\|/(?!\w))', cmd)
        mentions_parent = ".." in cmd
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
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ToolError(f"command timed out after {timeout}s: {command}") from exc
    code = proc.returncode
    text = out.decode("utf-8", "replace")
    return _clip(f"$ {command}\n[exit {code}]\n{text}")


async def execute(name: str, arguments: dict[str, Any]) -> str:
    if name == "read_file":
        p = resolve_path(str(arguments.get("path", "")))
        return _clip(_read(p, int(arguments.get("offset", 0)), int(arguments.get("limit", 2000))))
    if name == "write_file":
        p = resolve_path(str(arguments.get("path", "")))
        return _write(p, str(arguments.get("content", "")))
    if name == "edit_file":
        p = resolve_path(str(arguments.get("path", "")))
        return _edit(
            p,
            str(arguments.get("old_text", "")),
            str(arguments.get("new_text", "")),
            bool(arguments.get("replace_all", False)),
        )
    if name == "list_dir":
        p = resolve_path(str(arguments.get("path", ".")))
        return _list_dir(p)
    if name == "search_files":
        p = resolve_path(str(arguments.get("path", ".")))
        return _search(
            str(arguments.get("pattern", "")),
            p,
            str(arguments.get("glob", "*")),
            int(arguments.get("max_results", 50)),
        )
    if name == "run_shell":
        return await _run_shell(
            str(arguments.get("command", "")),
            float(arguments.get("timeout", config.AGENT_SHELL_TIMEOUT)),
        )
    raise ToolError(
        f"unknown tool: {name}. Available tools: "
        + ", ".join(t["function"]["name"] for t in TOOLS)
    )


def to_openai_tools() -> list[dict]:
    return TOOLS
