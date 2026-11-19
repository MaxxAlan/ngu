"""SQLite storage for hierarchical memory.

One database (``data/memory.db``): metadata, FTS5, optional sqlite-vec, and
embedding BLOBs. If the sqlite-vec extension cannot be loaded, inserts still
succeed and retrieval uses cosine-over-BLOB, then BM25-only.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app import config
from app.core.memory.embeddings import DIM, pack_f32, unpack_f32
from app.core.memory.types import MemoryEntry

log = logging.getLogger("local-agents.memory.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding BLOB,
    source_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    last_verified_at TEXT,
    superseded_by TEXT,
    key TEXT,
    tags TEXT NOT NULL DEFAULT '',
    task_id TEXT,
    subtask_id TEXT,
    verified_by TEXT,
    expires_at TEXT,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memory_entries_type ON memory_entries(type, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_entries_key ON memory_entries(key);
CREATE INDEX IF NOT EXISTS idx_memory_entries_task ON memory_entries(task_id, subtask_id);
CREATE INDEX IF NOT EXISTS idx_memory_entries_super ON memory_entries(superseded_by);

CREATE TABLE IF NOT EXISTS memory_links (
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (from_id, to_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_memory_links_to ON memory_links(to_id);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_entry_fts USING fts5(
    entry_id UNINDEXED,
    key,
    tags,
    content,
    tokenize='unicode61'
);
"""

_db_override: Path | None = None
_vec_state: bool | None = None


def db_path() -> Path:
    if _db_override is not None:
        return _db_override
    return config.DATA_DIR / "memory.db"


@contextmanager
def override_db(path: Path) -> Iterator[None]:
    global _db_override
    previous = _db_override
    _db_override = path
    try:
        yield
    finally:
        _db_override = previous


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    try:
        ensure_schema(con)
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def ensure_schema(con: sqlite3.Connection) -> None:
    vec_ok = _prepare_vec(con)
    con.executescript(_SCHEMA)
    if vec_ok:
        try:
            con.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(embedding float[{DIM}])"
            )
        except sqlite3.OperationalError as exc:
            log.warning(
                "sqlite-vec table init failed (%s); vector search will use cosine-over-BLOB "
                "and degrade to BM25-only if that fails",
                exc,
            )
            global _vec_state
            _vec_state = False


def vec_ready(con: sqlite3.Connection) -> bool:
    del con
    return _vec_state is True


def _prepare_vec(con: sqlite3.Connection) -> bool:
    global _vec_state
    if _vec_state is False:
        return False
    try:
        import sqlite_vec

        con.enable_load_extension(True)
        sqlite_vec.load(con)
        _vec_state = True
        return True
    except Exception as exc:  # noqa: BLE001
        if _vec_state is None:
            log.warning(
                "sqlite-vec is not available (%s). Vector search uses cosine over the "
                "embedding BLOB and degrades to BM25-only if scoring fails. Retrieval "
                "will not crash.",
                exc,
            )
        _vec_state = False
        return False


def reset_vec_state() -> None:
    """Test hook: forget a previous failed sqlite-vec probe."""
    global _vec_state
    _vec_state = None


def insert_entry(con: sqlite3.Connection, entry: MemoryEntry) -> None:
    blob = pack_f32(entry.embedding) if entry.embedding else None
    con.execute(
        """
        INSERT INTO memory_entries (
            id, type, content, embedding, source_json, confidence, created_at,
            last_verified_at, superseded_by, key, tags, task_id, subtask_id,
            verified_by, expires_at, success_count, failure_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.id,
            entry.type,
            entry.content,
            blob,
            json.dumps(entry.source_json, ensure_ascii=False),
            float(entry.confidence),
            entry.created_at,
            entry.last_verified_at,
            entry.superseded_by,
            entry.key,
            entry.tags or "",
            entry.task_id,
            entry.subtask_id,
            entry.verified_by,
            entry.expires_at,
            int(entry.success_count),
            int(entry.failure_count),
        ),
    )
    con.execute(
        "INSERT INTO memory_entry_fts(entry_id, key, tags, content) VALUES (?, ?, ?, ?)",
        (entry.id, entry.key or "", entry.tags or "", entry.content),
    )
    if blob and vec_ready(con):
        try:
            import sqlite_vec

            row = con.execute(
                "SELECT rowid FROM memory_entries WHERE id = ?",
                (entry.id,),
            ).fetchone()
            if row is not None:
                con.execute(
                    "INSERT INTO memory_vec(rowid, embedding) VALUES (?, ?)",
                    (int(row["rowid"]), sqlite_vec.serialize_float32(entry.embedding or [])),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "sqlite-vec insert failed for %s (%s); keeping the BLOB and continuing",
                entry.id,
                exc,
            )


def read_entry(con: sqlite3.Connection, entry_id: str) -> MemoryEntry | None:
    row = con.execute("SELECT * FROM memory_entries WHERE id = ?", (entry_id,)).fetchone()
    return _row_to_entry(row) if row else None


def get_entry(entry_id: str) -> MemoryEntry | None:
    with connect() as con:
        return read_entry(con, entry_id)


def active_by_key(key: str) -> list[MemoryEntry]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM memory_entries
            WHERE key = ? AND superseded_by IS NULL
            ORDER BY created_at DESC
            """,
            (key,),
        ).fetchall()
        return [_row_to_entry(row) for row in rows]


def find_by_origin(origin: str, key: str | None = None) -> MemoryEntry | None:
    with connect() as con:
        if key is None:
            row = con.execute(
                """
                SELECT * FROM memory_entries
                WHERE json_extract(source_json, '$.origin') = ?
                  AND superseded_by IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (origin,),
            ).fetchone()
        else:
            row = con.execute(
                """
                SELECT * FROM memory_entries
                WHERE key = ?
                  AND json_extract(source_json, '$.origin') = ?
                  AND superseded_by IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (key, origin),
            ).fetchone()
        return _row_to_entry(row) if row else None


def set_superseded(con: sqlite3.Connection, entry_id: str, new_id: str) -> None:
    con.execute(
        "UPDATE memory_entries SET superseded_by = ? WHERE id = ?",
        (new_id, entry_id),
    )


def set_expiry(con: sqlite3.Connection, entry_id: str, expires_at: str) -> None:
    con.execute(
        "UPDATE memory_entries SET expires_at = ? WHERE id = ?",
        (expires_at, entry_id),
    )


def set_counts(
    con: sqlite3.Connection,
    entry_id: str,
    success_count: int,
    failure_count: int,
    last_verified_at: str,
) -> None:
    con.execute(
        """
        UPDATE memory_entries
        SET success_count = ?, failure_count = ?, last_verified_at = ?
        WHERE id = ?
        """,
        (success_count, failure_count, last_verified_at, entry_id),
    )


def add_link(
    con: sqlite3.Connection,
    from_id: str,
    to_id: str,
    relation: str,
    weight: float,
) -> None:
    con.execute(
        """
        INSERT INTO memory_links(from_id, to_id, relation, weight) VALUES (?, ?, ?, ?)
        ON CONFLICT(from_id, to_id, relation) DO UPDATE SET weight = excluded.weight
        """,
        (from_id, to_id, relation, float(weight)),
    )


def links_from(entry_id: str) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT from_id, to_id, relation, weight FROM memory_links
            WHERE from_id = ? OR to_id = ?
            """,
            (entry_id, entry_id),
        ).fetchall()
        return [dict(row) for row in rows]


def neighbor_edges(con: sqlite3.Connection, ids: list[str]) -> list[tuple[str, str, str, float]]:
    """1-hop edges as (neighbor_id, parent_id, relation, weight)."""
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    rows = con.execute(
        f"""
        SELECT from_id, to_id, relation, weight FROM memory_links
        WHERE from_id IN ({marks}) OR to_id IN ({marks})
        """,
        [*ids, *ids],
    ).fetchall()
    seed = set(ids)
    edges: list[tuple[str, str, str, float]] = []
    for row in rows:
        src = row["from_id"]
        dst = row["to_id"]
        weight = float(row["weight"])
        relation = row["relation"]
        if src in seed and dst != src:
            edges.append((dst, src, relation, weight))
        if dst in seed and src != dst:
            edges.append((src, dst, relation, weight))
    return edges


def delete_entry(con: sqlite3.Connection, entry_id: str) -> None:
    row = con.execute(
        "SELECT rowid FROM memory_entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    if row is not None and vec_ready(con):
        try:
            con.execute("DELETE FROM memory_vec WHERE rowid = ?", (int(row["rowid"]),))
        except sqlite3.OperationalError:
            pass
    con.execute("DELETE FROM memory_entry_fts WHERE entry_id = ?", (entry_id,))
    con.execute(
        "DELETE FROM memory_links WHERE from_id = ? OR to_id = ?",
        (entry_id, entry_id),
    )
    con.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))


def delete_by_key(key: str) -> int:
    with connect() as con:
        rows = con.execute("SELECT id FROM memory_entries WHERE key = ?", (key,)).fetchall()
        for row in rows:
            delete_entry(con, row["id"])
        return len(rows)


def delete_working(con: sqlite3.Connection, subtask_id: str) -> int:
    rows = con.execute(
        "SELECT id FROM memory_entries WHERE type = 'working' AND subtask_id = ?",
        (subtask_id,),
    ).fetchall()
    for row in rows:
        delete_entry(con, row["id"])
    return len(rows)


def list_working(subtask_id: str) -> list[MemoryEntry]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM memory_entries
            WHERE type = 'working' AND subtask_id = ? AND superseded_by IS NULL
            ORDER BY created_at ASC
            """,
            (subtask_id,),
        ).fetchall()
        return [_row_to_entry(row) for row in rows]


def load_many(con: sqlite3.Connection, ids: list[str]) -> dict[str, MemoryEntry]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = con.execute(
        f"SELECT * FROM memory_entries WHERE id IN ({marks})",
        ids,
    ).fetchall()
    return {row["id"]: _row_to_entry(row) for row in rows}


def fts_query(raw: str) -> str:
    """Safe FTS5 MATCH expression (AND of prefix tokens)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        phrase = raw[1:-1].strip()
        return f'"{phrase}"' if phrase else ""
    tokens = re.findall(r"[\w\u0080-\uffff]+", raw, flags=re.UNICODE)
    if not tokens:
        safe = raw.replace('"', "").replace("*", "")
        return f'"{safe}"' if safe else ""
    parts = [f"{tok}*" for tok in tokens if len(tok) >= 2]
    if not parts:
        return tokens[0]
    return " AND ".join(parts)


def bm25_search(con: sqlite3.Connection, match: str, limit: int) -> list[str]:
    if not match:
        return []
    try:
        rows = con.execute(
            """
            SELECT entry_id, bm25(memory_entry_fts) AS score
            FROM memory_entry_fts
            WHERE memory_entry_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (match, int(limit)),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        log.warning("FTS query failed (%s); skipping BM25 for this request", exc)
        return []
    return [row["entry_id"] for row in rows]


def vector_search(con: sqlite3.Connection, query: str, limit: int) -> list[str]:
    """Rank entry ids by cosine. Empty list means 'skip the vector ranker'."""
    from app.core.memory.embeddings import cosine, safe_embed

    query_vec = safe_embed(query)
    if not query_vec:
        log.warning("no query embedding; degrading this request to BM25-only")
        return []
    if vec_ready(con):
        ranked = _vec_knn(con, query_vec, limit)
        if ranked is not None:
            return ranked
    try:
        return _cosine_scan(con, query_vec, limit, cosine)
    except Exception as exc:  # noqa: BLE001
        log.warning("cosine scan failed (%s); degrading this request to BM25-only", exc)
        return []


def _vec_knn(con: sqlite3.Connection, query_vec: list[float], limit: int) -> list[str] | None:
    try:
        import sqlite_vec

        blob = sqlite_vec.serialize_float32(query_vec)
        rows = con.execute(
            """
            SELECT e.id AS id
            FROM memory_vec AS v
            JOIN memory_entries AS e ON e.rowid = v.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
            """,
            (blob, int(limit)),
        ).fetchall()
        return [row["id"] for row in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "sqlite-vec query failed (%s); falling back to cosine-over-BLOB "
            "(BM25-only if that fails too)",
            exc,
        )
        return None


def _cosine_scan(
    con: sqlite3.Connection,
    query_vec: list[float],
    limit: int,
    cosine_fn: Any,
) -> list[str]:
    scored: list[tuple[float, str]] = []
    rows = con.execute(
        "SELECT id, embedding FROM memory_entries WHERE embedding IS NOT NULL"
    ).fetchall()
    for row in rows:
        vec = unpack_f32(row["embedding"])
        if not vec:
            continue
        scored.append((cosine_fn(query_vec, vec), row["id"]))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [entry_id for _score, entry_id in scored[:limit]]


def is_visible(
    entry: MemoryEntry,
    *,
    task_id: str | None,
    subtask_id: str | None,
    now: str,
) -> bool:
    if entry.superseded_by:
        return False
    if entry.expires_at and entry.expires_at <= now:
        return False
    if entry.type == "working":
        return bool(subtask_id) and entry.subtask_id == subtask_id
    if task_id and entry.type not in {"semantic", "procedural"}:
        if entry.task_id and entry.task_id != task_id:
            return False
    return True


def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
    source = row["source_json"] or "{}"
    try:
        parsed = json.loads(source)
    except json.JSONDecodeError:
        parsed = {"origin": "unknown", "offset": 0, "timestamp": ""}
    if not isinstance(parsed, dict):
        parsed = {"origin": "unknown", "offset": 0, "timestamp": ""}
    return MemoryEntry(
        id=row["id"],
        type=row["type"],
        content=row["content"],
        source_json=parsed,
        confidence=float(row["confidence"]),
        created_at=row["created_at"],
        last_verified_at=row["last_verified_at"],
        superseded_by=row["superseded_by"],
        embedding=None,
        key=row["key"],
        tags=row["tags"] or "",
        task_id=row["task_id"],
        subtask_id=row["subtask_id"],
        verified_by=row["verified_by"],
        expires_at=row["expires_at"],
        success_count=int(row["success_count"] or 0),
        failure_count=int(row["failure_count"] or 0),
    )
