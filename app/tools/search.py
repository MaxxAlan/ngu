from __future__ import annotations

import re

from .common import ToolError, clip, rel_or_abs, resolve_path


def search_files(pattern: str, path: str = ".", glob: str = "*", max_results: int = 50) -> str:
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"no such path: {p}")
    if not pattern:
        raise ToolError("pattern is required")
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"bad regex: {exc}") from exc
    files = [p] if p.is_file() else list(p.rglob(glob or "*"))
    max_results = max(1, min(int(max_results), 500))
    hits: list[str] = []
    for f in files:
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = rel_or_abs(f)
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:300]}")
                if len(hits) >= max_results:
                    return clip("\n".join(hits) + f"\n... limit reached ({max_results})")
    if not hits:
        return f"no matches for /{pattern}/ under {p}"
    return clip("\n".join(hits))
