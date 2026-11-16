"""Tests for persistent memory tools (SQLite FTS5)."""
from __future__ import annotations

import time

import pytest

from app import config
from app.tools import memory
from app.tools.common import ToolError


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    """Isolated memory.db per test."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    memory._MEM_DB = None
    memory._INITIALIZED = False
    yield data / "memory.db"
    memory._MEM_DB = None
    memory._INITIALIZED = False


def test_save_get_search_list_delete(mem_db):
    out = memory.memory_save("design_notes", "Use SQLite FTS5 for local search.", tags="arch backend")
    assert "memory saved" in out
    assert "design_notes" in out

    got = memory.memory_get("design_notes")
    assert "SQLite FTS5" in got
    assert "design_notes" in got

    found = memory.memory_search("FTS5")
    assert "design_notes" in found
    assert "SQLite" in found

    found_tag = memory.memory_search("search", tags="arch")
    assert "design_notes" in found_tag

    listed = memory.memory_list()
    assert "design_notes" in listed

    deleted = memory.memory_delete("design_notes")
    assert "deleted" in deleted
    with pytest.raises(ToolError):
        memory.memory_get("design_notes")


def test_save_update_overwrites(mem_db):
    memory.memory_save("k", "v1", tags="t1")
    memory.memory_save("k", "v2 updated", tags="t2")
    got = memory.memory_get("k")
    assert "v2 updated" in got
    assert "t2" in got
    assert "v1" not in got


def test_search_empty_query_raises(mem_db):
    with pytest.raises(ToolError):
        memory.memory_search("")
    with pytest.raises(ToolError):
        memory.memory_save("", "x")
    with pytest.raises(ToolError):
        memory.memory_save("k", "  ")


def test_search_no_hits(mem_db):
    memory.memory_save("a", "hello world")
    out = memory.memory_search("zzzznotfound")
    assert "no memory entries found" in out


def test_prune_by_count(mem_db):
    for i in range(5):
        memory.memory_save(f"item{i}", f"content {i}", tags="n")
        time.sleep(0.01)
    msg = memory.memory_prune(max_entries=2, max_age_days=9999)
    assert "pruned" in msg
    listed = memory.memory_list(limit=20)
    # only 2 newest remain
    assert listed.count("item") <= 2


def test_prune_checkpoint_age(mem_db):
    memory.memory_save("task:1:progress", "old checkpoint", tags="checkpoint")
    # force old timestamp
    con = memory._connect()
    try:
        memory._ensure_schema(con)
        con.execute(
            "UPDATE memory_meta SET updated = ? WHERE key = ?",
            (time.time() - 40 * 86400, "task:1:progress"),
        )
        con.commit()
    finally:
        con.close()
    memory.memory_prune(max_entries=2000, max_age_days=30)
    with pytest.raises(ToolError):
        memory.memory_get("task:1:progress")


def test_list_tag_filter(mem_db):
    memory.memory_save("py", "python tip", tags="python tips")
    memory.memory_save("go", "golang tip", tags="go tips")
    out = memory.memory_list(tags="python")
    assert "py" in out
    assert "go" not in out
