"""Migration from a pre-hierarchical FTS memory.db."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from app.core.memory.migrate import migrate_db

ROOT = Path(__file__).resolve().parents[3]
_LEGACY = """
CREATE TABLE memory_kv (
    key TEXT PRIMARY KEY,
    content TEXT NOT NULL
);
CREATE TABLE memory_meta (
    key TEXT PRIMARY KEY,
    tags TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE VIRTUAL TABLE memory_fts USING fts5(
    key, tags, content, tokenize='unicode61'
);
"""


def _legacy_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.executescript(_LEGACY)
        now = time.time()
        rows = [
            ("design_notes", "Use SQLite FTS5 for local search.", "arch"),
            ("recipe", "Feed the sourdough starter every morning.", "bake"),
            ("orbit", "Keplerian elements describe the spacecraft orbit.", "space"),
        ]
        for key, content, tags in rows:
            con.execute("INSERT INTO memory_kv(key, content) VALUES (?, ?)", (key, content))
            con.execute(
                "INSERT INTO memory_meta(key, tags, created, updated) VALUES (?, ?, ?, ?)",
                (key, tags, now, now),
            )
            con.execute(
                "INSERT INTO memory_fts(key, tags, content) VALUES (?, ?, ?)",
                (key, tags, content),
            )
        con.commit()
    finally:
        con.close()


def test_migrate_legacy_db_is_idempotent(mem_env, tmp_path):
    db = tmp_path / "memory.db"
    _legacy_db(db)
    first = migrate_db(db)
    assert first["source_rows"] == 3
    assert first["copied"] == 3
    assert first["skipped"] == 0

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT type, confidence, content, source_json FROM memory_entries ORDER BY key"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 3
    for row in rows:
        assert row["type"] == "episodic"
        assert row["confidence"] == 1.0
        source = json.loads(row["source_json"])
        assert source["origin"] == "tool:memory_kv"
        assert "offset" in source
        assert source["timestamp"]

    second = migrate_db(db)
    assert second["copied"] == 0
    assert second["skipped"] == 3

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "migrate_memory.py"), "--db", str(db)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["skipped"] == 3
    assert payload["copied"] == 0


def test_migrate_missing_table(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    result = migrate_db(db)
    assert result["source_rows"] == 0
    assert result["copied"] == 0
