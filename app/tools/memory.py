"""
Lightweight persistent memory for the agent.
Uses SQLite FTS5 full-text search – no embedding model required.

Tools exposed:
  memory_save(key, content, tags="")  → store / update a note
  memory_get(key)                      → exact lookup by key
  memory_search(query, limit=5, tags="") → keyword / FTS search
  memory_list(limit=20, tags="")        → list keys (optional tag filter)
  memory_delete(key)                   → remove one entry
  memory_prune(...)                    → internal; bound size / age
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from .. import config
from .common import ToolError, clip

_MEM_DB: Path | None = None
_INITIALIZED = False


def _db_path() -> Path:
    global _MEM_DB
    if _MEM_DB is None:
        _MEM_DB = config.DATA_DIR / "memory.db"
    return _MEM_DB


def _connect() -> sqlite3.Connection:
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS memory_kv (
            key     TEXT PRIMARY KEY,
            content TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_meta (
            key     TEXT PRIMARY KEY,
            tags    TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL,
            updated REAL NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
            USING fts5(
                key,
                tags,
                content,
                tokenize='unicode61'
            );
        """
    )
    # Rebuild FTS from kv+meta if empty but data exists (migration / old trigger mess)
    fts_count = con.execute("SELECT count(*) FROM memory_fts").fetchone()[0]
    kv_count = con.execute("SELECT count(*) FROM memory_kv").fetchone()[0]
    if kv_count > 0 and fts_count == 0:
        con.execute("DELETE FROM memory_fts")
        con.execute(
            """
            INSERT INTO memory_fts(key, tags, content)
            SELECT k.key, COALESCE(m.tags, ''), k.content
            FROM memory_kv k
            LEFT JOIN memory_meta m ON k.key = m.key
            """
        )
        con.commit()
    _INITIALIZED = True


def _init_db() -> None:
    con = _connect()
    try:
        _ensure_schema(con)
        con.commit()
    finally:
        con.close()


def _sync_fts(con: sqlite3.Connection, key: str, tags: str, content: str) -> None:
    con.execute("DELETE FROM memory_fts WHERE key = ?", (key,))
    con.execute(
        "INSERT INTO memory_fts(key, tags, content) VALUES (?, ?, ?)",
        (key, tags, content),
    )


def _fts_query(raw: str) -> str:
    """Turn user text into a safe FTS5 MATCH expression (AND of tokens, phrase if quoted)."""
    raw = raw.strip()
    if not raw:
        return ""
    # quoted phrase
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        phrase = raw[1:-1].strip()
        if phrase:
            return f'"{phrase}"'
    tokens = re.findall(r"[\w\u0080-\uffff]+", raw, flags=re.UNICODE)
    if not tokens:
        safe = raw.replace('"', "").replace("*", "")
        return f'"{safe}"' if safe else ""
    parts: list[str] = []
    for t in tokens:
        if len(t) < 2:
            continue
        parts.append(f"{t}*")
    return " AND ".join(parts) if parts else tokens[0]


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

def memory_save(key: str, content: str, tags: str = "") -> str:
    """Save or update a memory entry. key is a short identifier."""
    if not key or not key.strip():
        raise ToolError("key must not be empty")
    if not content or not content.strip():
        raise ToolError("content must not be empty")
    key = key.strip()[:200]
    tags = (tags or "").strip()[:500]
    content = content.strip()
    now = time.time()
    con = _connect()
    try:
        _ensure_schema(con)
        with con:
            con.execute(
                "INSERT INTO memory_kv(key, content) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET content = excluded.content",
                (key, content),
            )
            con.execute(
                "INSERT INTO memory_meta(key, tags, created, updated) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET tags = excluded.tags, updated = excluded.updated",
                (key, tags, now, now),
            )
            _sync_fts(con, key, tags, content)
        return f"memory saved: {key!r} ({len(content)} chars)"
    finally:
        con.close()


def memory_get(key: str) -> str:
    """Return the full content of one memory entry by exact key."""
    if not key or not key.strip():
        raise ToolError("key must not be empty")
    key = key.strip()
    con = _connect()
    try:
        _ensure_schema(con)
        row = con.execute(
            """
            SELECT m.key, COALESCE(mm.tags, '') AS tags, m.content, mm.updated
            FROM memory_kv m
            LEFT JOIN memory_meta mm ON m.key = mm.key
            WHERE m.key = ?
            """,
            (key,),
        ).fetchone()
        if not row:
            raise ToolError(f"no memory entry with key: {key!r}")
        tag_str = f" [{row['tags']}]" if row["tags"] else ""
        updated = ""
        if row["updated"]:
            updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["updated"]))
            updated = f" (updated {updated})"
        body = f"### {row['key']}{tag_str}{updated}\n{row['content']}"
        return clip(body)
    finally:
        con.close()


def memory_search(query: str, limit: int = 5, tags: str = "") -> str:
    """Search memories by keyword. Optional tags filter (substring match on tags column)."""
    if not query or not query.strip():
        raise ToolError("query must not be empty")
    limit = max(1, min(int(limit), 50))
    tag_filter = (tags or "").strip().lower()
    con = _connect()
    try:
        _ensure_schema(con)
        rows: list = []
        match_expr = _fts_query(query)
        if match_expr:
            try:
                sql = (
                    "SELECT memory_fts.key, memory_meta.tags, memory_kv.content, memory_meta.updated, "
                    "bm25(memory_fts) AS score "
                    "FROM memory_fts "
                    "JOIN memory_meta ON memory_fts.key = memory_meta.key "
                    "JOIN memory_kv ON memory_fts.key = memory_kv.key "
                    "WHERE memory_fts MATCH ? "
                )
                params: list = [match_expr]
                if tag_filter:
                    sql += "AND lower(memory_meta.tags) LIKE ? "
                    params.append(f"%{tag_filter}%")
                sql += "ORDER BY score LIMIT ?"
                params.append(limit)
                rows = con.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            # LIKE fallback (also used when FTS returns nothing)
            like = f"%{query.strip()}%"
            sql = (
                "SELECT m.key, mm.tags, m.content, mm.updated, 0 AS score "
                "FROM memory_kv m JOIN memory_meta mm ON m.key = mm.key "
                "WHERE (m.content LIKE ? OR mm.tags LIKE ? OR m.key LIKE ?) "
            )
            params = [like, like, like]
            if tag_filter:
                sql += "AND lower(mm.tags) LIKE ? "
                params.append(f"%{tag_filter}%")
            sql += "ORDER BY mm.updated DESC LIMIT ?"
            params.append(limit)
            rows = con.execute(sql, params).fetchall()
        if not rows:
            return f"no memory entries found for query: {query!r}"
        out: list[str] = []
        for r in rows:
            snippet = str(r["content"])
            if len(snippet) > 500:
                snippet = snippet[:500] + "…"
            tag_str = f" [{r['tags']}]" if r["tags"] else ""
            out.append(f"### {r['key']}{tag_str}\n{snippet}")
        return clip("\n\n".join(out))
    finally:
        con.close()


def memory_list(limit: int = 20, tags: str = "") -> str:
    """List saved memory keys with tags, size, and update time."""
    limit = max(1, min(int(limit), 200))
    tag_filter = (tags or "").strip().lower()
    con = _connect()
    try:
        _ensure_schema(con)
        sql = (
            "SELECT mm.key, mm.tags, length(m.content) AS size, mm.updated "
            "FROM memory_meta mm JOIN memory_kv m ON mm.key = m.key "
        )
        params: list = []
        if tag_filter:
            sql += "WHERE lower(mm.tags) LIKE ? "
            params.append(f"%{tag_filter}%")
        sql += "ORDER BY mm.updated DESC LIMIT ?"
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
        if not rows:
            return "no memories saved yet"
        lines = ["key | tags | size | updated"]
        for r in rows:
            t = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["updated"]))
            lines.append(f"{r['key']!r:30} {r['tags']!r:20} {r['size']:>6}c  {t}")
        return "\n".join(lines)
    finally:
        con.close()


def memory_prune(max_entries: int | None = None, max_age_days: int | None = None) -> str:
    """Delete oldest entries beyond max_entries, and checkpoint entries older than max_age_days.

    Not exposed to the model by default — call at startup to keep memory.db bounded.
    """
    if max_entries is None:
        max_entries = int(getattr(config, "MEMORY_MAX_ENTRIES", 2000) or 2000)
    if max_age_days is None:
        max_age_days = int(getattr(config, "MEMORY_MAX_AGE_DAYS", 30) or 30)
    max_entries = max(0, int(max_entries))
    max_age_days = max(0, int(max_age_days))
    con = _connect()
    try:
        _ensure_schema(con)
        cutoff = time.time() - max_age_days * 86400
        deleted = 0
        with con:
            stale = con.execute(
                "SELECT key FROM memory_meta WHERE tags LIKE '%checkpoint%' AND updated < ?",
                (cutoff,),
            ).fetchall()
            excess = con.execute(
                "SELECT key FROM memory_meta ORDER BY updated DESC LIMIT -1 OFFSET ?",
                (max_entries,),
            ).fetchall()
            keys = {r["key"] for r in stale} | {r["key"] for r in excess}
            for key in keys:
                con.execute("DELETE FROM memory_meta WHERE key = ?", (key,))
                con.execute("DELETE FROM memory_kv WHERE key = ?", (key,))
                con.execute("DELETE FROM memory_fts WHERE key = ?", (key,))
                deleted += 1
        return f"pruned {deleted} memory entries"
    finally:
        con.close()


def memory_delete(key: str) -> str:
    """Delete a memory entry by key."""
    if not key or not key.strip():
        raise ToolError("key must not be empty")
    key = key.strip()
    con = _connect()
    try:
        _ensure_schema(con)
        with con:
            rows_deleted = con.execute("DELETE FROM memory_meta WHERE key = ?", (key,)).rowcount
            con.execute("DELETE FROM memory_kv WHERE key = ?", (key,))
            con.execute("DELETE FROM memory_fts WHERE key = ?", (key,))
        if rows_deleted == 0:
            raise ToolError(f"no memory entry with key: {key!r}")
        return f"deleted memory: {key!r}"
    finally:
        con.close()
