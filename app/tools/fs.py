from __future__ import annotations

import fnmatch
import shutil
from pathlib import Path

from .common import ToolError, clip, resolve_path


def read_file(path: str, offset: int = 0, limit: int = 400) -> str:
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"no such file: {p}")
    if p.is_dir():
        raise ToolError(f"{p} is a directory")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"cannot read {p}: {exc}") from exc
    lines = text.splitlines()
    total = len(lines)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 2000))
    chunk = lines[offset : offset + limit]
    body = "\n".join(f"{i + offset + 1}: {line}" for i, line in enumerate(chunk))
    header = f"{p} ({total} lines, showing {offset + 1}-{offset + len(chunk)})"
    return clip(f"{header}\n{body}")


def write_file(path: str, content: str) -> str:
    p = resolve_path(path)
    if p.is_dir():
        raise ToolError(f"path is a directory, give a file name: {p}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"cannot write {p}: {exc}") from exc
    return f"wrote {len(content)} chars to {p}"


def append_file(path: str, content: str) -> str:
    """Append text to a file (creates it when missing)."""
    p = resolve_path(path)
    if p.is_dir():
        raise ToolError(f"path is a directory, give a file name: {p}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        raise ToolError(f"cannot append to {p}: {exc}") from exc
    return f"appended {len(content)} chars to {p}"


def edit_file(path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"no such file: {p}")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"cannot read {p}: {exc}") from exc
    if old_text == "":
        raise ToolError("old_text must not be empty")
    count = text.count(old_text)
    if count == 0:
        raise ToolError(f"old_text not found in {p}")
    if count > 1 and not replace_all:
        raise ToolError(f"old_text found {count} times in {p}; provide more context or set replace_all=true")
    updated = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
    p.write_text(updated, encoding="utf-8")
    return f"edited {p} ({count if replace_all else 1} replacement(s), {count} match(es))"


def list_dir(path: str = ".") -> str:
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"no such directory: {p}")
    if p.is_file():
        raise ToolError(f"{p} is a file, not a directory")
    try:
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except OSError as exc:
        raise ToolError(f"cannot list {p}: {exc}") from exc
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
    return clip(f"{p} ({len(entries)} entries)\n" + "\n".join(lines) + more)


def list_tree(path: str = ".", max_depth: int = 3, max_entries: int = 400) -> str:
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"no such path: {p}")
    if p.is_file():
        return str(p)
    max_depth = max(1, min(int(max_depth), 8))
    max_entries = max(20, min(int(max_entries), 2000))
    lines: list[str] = [str(p)]
    count = 0
    truncated = False

    def walk(dir_path: Path, prefix: str, depth: int) -> None:
        nonlocal count, truncated
        if depth > max_depth or truncated:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except OSError:
            return
        # skip noisy dirs
        filtered = [e for e in entries if e.name not in {".git", "__pycache__", "node_modules", ".venv", "venv"}]
        for idx, e in enumerate(filtered):
            if count >= max_entries:
                truncated = True
                return
            last = idx == len(filtered) - 1
            branch = "└── " if last else "├── "
            try:
                is_dir = e.is_dir()
            except OSError:
                is_dir = False
            label = e.name + ("/" if is_dir else "")
            lines.append(f"{prefix}{branch}{label}")
            count += 1
            if is_dir:
                ext = "    " if last else "│   "
                walk(e, prefix + ext, depth + 1)

    walk(p, "", 1)
    if truncated:
        lines.append(f"... truncated at {max_entries} entries")
    return clip("\n".join(lines))


def glob_files(pattern: str = "*", path: str = ".", max_results: int = 200) -> str:
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"no such path: {p}")
    max_results = max(1, min(int(max_results), 2000))
    pattern = (pattern or "*").replace("\\", "/")
    base = p if p.is_dir() else p.parent
    files = [p] if p.is_file() else list(base.rglob("*"))
    hits: list[str] = []
    for f in files:
        try:
            rel = f.resolve().relative_to(base.resolve()).as_posix()
        except Exception:  # noqa: BLE001
            rel = f.name
        name = f.name
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, f"**/{pattern}"):
            kind = "dir" if f.is_dir() else "file"
            hits.append(f"{kind:4} {rel}")
            if len(hits) >= max_results:
                return clip("\n".join(hits) + f"\n... limit reached ({max_results})")
    if not hits:
        return f"no matches for pattern '{pattern}' under {base}"
    return clip("\n".join(hits))


def make_dir(path: str) -> str:
    p = resolve_path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolError(f"cannot create directory {p}: {exc}") from exc
    return f"created directory {p}"


def remove_path(path: str, recursive: bool = False) -> str:
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"no such path: {p}")
    try:
        if p.is_dir() and not p.is_symlink():
            if not recursive:
                p.rmdir()
            else:
                shutil.rmtree(p)
        else:
            p.unlink()
    except OSError as exc:
        raise ToolError(f"cannot remove {p}: {exc}") from exc
    return f"removed {p}"


def move_path(src: str, dst: str) -> str:
    s = resolve_path(src)
    d = resolve_path(dst)
    if not s.exists():
        raise ToolError(f"no such path: {s}")
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
    except OSError as exc:
        raise ToolError(f"cannot move {s} -> {d}: {exc}") from exc
    return f"moved {s} -> {d}"
