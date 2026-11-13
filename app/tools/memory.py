"""
Lightweight persistent memory for the agent.
Uses SQLite FTS5 full-text search – no embedding model required.

Tools exposed:
  memory_save(key, content, tags="")  → store a note/snippet
  memory_search(query, limit=5)        → fuzzy-keyword search over saved memories
  memory_list(limit=20)                → list all keys
  memory_delete(key)                   → remove one entry
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .. import config
from .common import ToolError, clip

_MEM_DB: Path | None = None


def _db_path() -> Path:
    global _MEM_DB
    if _MEM_DB is None:
        _MEM_DB = config.DATA_DIR / "memory.db"
    return _MEM_DB


def _connect() -> sqlite3.Connection:
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    # FTS5 virtual table for full-text search
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS memory_meta (
            key       TEXT PRIMARY KEY,
            tags      TEXT NOT NULL DEFAULT '',
            created   REAL NOT NULL,
            updated   REAL NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
            USING fts5(key, tags, content, tokenize='unicode61');
        CREATE TRIGGER IF NOT EXISTS mem_meta_insert AFTER INSERT ON memory_meta BEGIN
            DELETE FROM memory_fts WHERE key = NEW.key;
            INSERT INTO memory_fts(key, tags, content) VALUES (NEW.key, NEW.tags, '');
        END;
        """
    )
    return con


def _init_db() -> None:
    with _connect():
        pass


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
    now = time.time()
    con = _connect()
    with con:
        # Store content in a separate kv table (FTS5 content= mode optional)
        con.execute(
            "CREATE TABLE IF NOT EXISTS memory_kv (key TEXT PRIMARY KEY, content TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO memory_kv(key, content) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET content=excluded.content",
            (key, content),
        )
        con.execute(
            "INSERT INTO memory_meta(key, tags, created, updated) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET tags=excluded.tags, updated=excluded.updated",
            (key, tags, now, now),
        )
        # Keep FTS5 in sync
        con.execute("DELETE FROM memory_fts WHERE key = ?", (key,))
        con.execute(
            "INSERT INTO memory_fts(key, tags, content) VALUES(?,?,?)",
            (key, tags, content),
        )
    return f"memory saved: {key!r} ({len(content)} chars)"


def memory_search(query: str, limit: int = 5) -> str:
    """Search memories by keyword. Returns matching entries."""
    if not query or not query.strip():
        raise ToolError("query must not be empty")
    limit = max(1, min(int(limit), 50))
    con = _connect()
    con.execute("CREATE TABLE IF NOT EXISTS memory_kv (key TEXT PRIMARY KEY, content TEXT NOT NULL)")
    try:
        # FTS5 match query
        rows = con.execute(
            "SELECT memory_fts.key, memory_meta.tags, memory_kv.content, memory_meta.updated "
            "FROM memory_fts "
            "JOIN memory_meta ON memory_fts.key = memory_meta.key "
            "JOIN memory_kv ON memory_fts.key = memory_kv.key "
            "WHERE memory_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (query.strip(), limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # FTS5 may not be available; fallback to LIKE search
        rows = con.execute(
            "SELECT m.key, mm.tags, m.content, mm.updated "
            "FROM memory_kv m JOIN memory_meta mm ON m.key = mm.key "
            "WHERE m.content LIKE ? OR mm.tags LIKE ? OR m.key LIKE ? "
            "ORDER BY mm.updated DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()
    if not rows:
        return f"no memory entries found for query: {query!r}"
    out: list[str] = []
    for r in rows:
        snippet = str(r["content"])[:400]
        tag_str = f" [{r['tags']}]" if r["tags"] else ""
        out.append(f"### {r['key']}{tag_str}\n{snippet}")
    return clip("\n\n".join(out))


def memory_list(limit: int = 20) -> str:
    """List all saved memory keys with tags and update time."""
    limit = max(1, min(int(limit), 200))
    con = _connect()
    con.execute("CREATE TABLE IF NOT EXISTS memory_kv (key TEXT PRIMARY KEY, content TEXT NOT NULL)")
    rows = con.execute(
        "SELECT mm.key, mm.tags, length(m.content) AS size, mm.updated "
        "FROM memory_meta mm JOIN memory_kv m ON mm.key = m.key "
        "ORDER BY mm.updated DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return "no memories saved yet"
    lines = ["key | tags | size | updated"]
    for r in rows:
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["updated"]))
        lines.append(f"{r['key']!r:30} {r['tags']!r:20} {r['size']:>6}c  {t}")
    return "\n".join(lines)


def memory_delete(key: str) -> str:
    """Delete a memory entry by key."""
    if not key or not key.strip():
        raise ToolError("key must not be empty")
    key = key.strip()
    con = _connect()
    con.execute("CREATE TABLE IF NOT EXISTS memory_kv (key TEXT PRIMARY KEY, content TEXT NOT NULL)")
    with con:
        rows_deleted = con.execute("DELETE FROM memory_meta WHERE key = ?", (key,)).rowcount
        con.execute("DELETE FROM memory_kv WHERE key = ?", (key,))
        con.execute("DELETE FROM memory_fts WHERE key = ?", (key,))
    if rows_deleted == 0:
        raise ToolError(f"no memory entry with key: {key!r}")
    return f"deleted memory: {key!r}"
