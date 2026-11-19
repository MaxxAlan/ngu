"""Copy legacy FTS memory rows into ``memory_entries``."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app import config
from app.core.memory import sqlite_store as store
from app.core.memory.types import make_source
from app.core.memory.writer import MemoryWriter

_LEGACY_ORIGIN = "tool:memory_kv"


def migrate_db(db_path: Path | str | None = None) -> dict[str, int | str]:
    """Copy ``memory_kv`` rows into hierarchical memory.

    Each copied row is ``type='episodic'`` with ``confidence=1.0``.
    Safe to re-run: an active row with the same key and legacy origin is skipped
    when its content is unchanged, or superseded when the legacy note changed.
    """
    target = Path(db_path) if db_path else (config.DATA_DIR / "memory.db")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        return {
            "db": str(target),
            "source_rows": 0,
            "copied": 0,
            "skipped": 0,
            "superseded": 0,
        }

    legacy = _read_legacy(target)
    copied = 0
    skipped = 0
    superseded = 0
    writer = MemoryWriter()
    with store.override_db(target):
        for row in legacy:
            key = row["key"]
            content = row["content"]
            existing = store.find_by_origin(_LEGACY_ORIGIN, key=key)
            if existing is not None and existing.content == content:
                skipped += 1
                continue
            timestamp = _iso(row["updated"] or row["created"])
            saved = writer.upsert_key(
                key=key,
                content=content,
                tags=row["tags"] or "",
                type="episodic",
                confidence=1.0,
                verified_by="migration",
                source=make_source(
                    _LEGACY_ORIGIN,
                    offset=0,
                    timestamp=timestamp,
                    key=key,
                    legacy=True,
                ),
            )
            if existing is not None and saved.id != existing.id:
                superseded += 1
            else:
                copied += 1
    return {
        "db": str(target),
        "source_rows": len(legacy),
        "copied": copied,
        "skipped": skipped,
        "superseded": superseded,
    }


def _read_legacy(path: Path) -> list[sqlite3.Row]:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        names = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        if "memory_kv" not in names:
            return []
        if "memory_meta" in names:
            return list(
                con.execute(
                    """
                    SELECT k.key AS key, k.content AS content,
                           COALESCE(m.tags, '') AS tags,
                           m.created AS created, m.updated AS updated
                    FROM memory_kv k
                    LEFT JOIN memory_meta m ON k.key = m.key
                    """
                ).fetchall()
            )
        return list(
            con.execute(
                """
                SELECT key, content, '' AS tags, NULL AS created, NULL AS updated
                FROM memory_kv
                """
            ).fetchall()
        )
    finally:
        con.close()


def _iso(value: float | None) -> str:
    if not value:
        return datetime.now(UTC).isoformat(timespec="seconds")
    try:
        return datetime.fromtimestamp(float(value), UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return datetime.now(UTC).isoformat(timespec="seconds")
