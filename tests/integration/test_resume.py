"""End-to-end style test: simulate crash mid-run and resume without redoing completed work."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="ngu-resume-")
os.environ["LA_DATA_DIR"] = _TMP
os.environ["LA_TASK_KERNEL"] = "on"


@pytest.fixture()
def kernel_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    monkeypatch.setenv("LA_DATA_DIR", str(db_dir))
    import app.config as cfg

    monkeypatch.setattr(cfg, "DATA_DIR", db_dir)
    monkeypatch.setattr(cfg, "DB_PATH", db_dir / "tasks.db")
    monkeypatch.setattr(cfg, "TASK_KERNEL", True)
    from app.core import task_kernel as tk

    tk.ensure_schema()
    return tk


def test_crash_and_resume_skips_completed(kernel_db):
    tk = kernel_db
    task = tk.create_task(
        "Write three files then stop",
        acceptance={"files": ["a.txt", "b.txt", "c.txt"]},
        status="running",
    )
    # Simulate three sequential tool subtasks (like the agent would create).
    s1 = tk.add_subtask(task.id, None, "tool:write_file a.txt", status="ready")
    s2 = tk.add_subtask(task.id, s1.id, "tool:write_file b.txt", depends_on=[s1.id], status="pending")
    s3 = tk.add_subtask(task.id, s2.id, "tool:write_file c.txt", depends_on=[s2.id], status="pending")

    # Complete first two; crash during third.
    tk.mark_subtask_status(s1.id, "in_progress")
    tk.store_tool_effect(f"{task.id}:{s1.id}:write_file", "write_file", {"path": "a.txt"}, "ok-a")
    tk.mark_subtask_status(s1.id, "completed")
    tk.checkpoint(task.id)

    tk.mark_subtask_status(s2.id, "in_progress")
    tk.store_tool_effect(f"{task.id}:{s2.id}:write_file", "write_file", {"path": "b.txt"}, "ok-b")
    tk.mark_subtask_status(s2.id, "completed")
    tk.checkpoint(task.id)

    tk.mark_subtask_status(s3.id, "in_progress")
    # CRASH: process dies before completing s3 / storing effect.
    # --- reopen ---
    state = tk.resume(task.id)
    assert s1.id in state.completed_subtask_ids
    assert s2.id in state.completed_subtask_ids
    assert s3.id not in state.completed_subtask_ids

    # Idempotent replay of completed tool must return cached result, not re-run.
    cached_a = tk.lookup_tool_effect(
        f"{task.id}:{s1.id}:write_file", "write_file", {"path": "a.txt"}
    )
    assert cached_a == "ok-a"
    cached_b = tk.lookup_tool_effect(
        f"{task.id}:{s2.id}:write_file", "write_file", {"path": "b.txt"}
    )
    assert cached_b == "ok-b"
    # s3 never stored → miss
    assert (
        tk.lookup_tool_effect(f"{task.id}:{s3.id}:write_file", "write_file", {"path": "c.txt"})
        is None
    )

    # Finish remaining work after resume.
    tk.store_tool_effect(f"{task.id}:{s3.id}:write_file", "write_file", {"path": "c.txt"}, "ok-c")
    tk.mark_subtask_status(s3.id, "completed")
    tk.set_task_status(task.id, "done")
    tk.checkpoint(task.id)

    final = tk.resume(task.id)
    assert set(final.completed_subtask_ids) == {s1.id, s2.id, s3.id}
    assert final.next_subtask is None
    assert final.task.status == "done"


def test_next_runnable_respects_deps(kernel_db):
    tk = kernel_db
    t = tk.create_task("deps")
    a = tk.add_subtask(t.id, None, "A", status="ready")
    b = tk.add_subtask(t.id, a.id, "B", depends_on=[a.id], status="pending")
    c = tk.add_subtask(t.id, None, "C", status="ready")  # independent
    nxt = tk.next_runnable_subtask(t.id)
    assert nxt is not None
    assert nxt.id in {a.id, c.id}
    tk.mark_subtask_status(a.id, "in_progress")
    tk.mark_subtask_status(a.id, "completed")
    # Now B becomes runnable
    runnable_ids = set()
    # drain ready ones
    seen = set()
    while True:
        n = tk.next_runnable_subtask(t.id)
        if n is None or n.id in seen:
            break
        seen.add(n.id)
        runnable_ids.add(n.id)
        if n.status == "pending":
            tk.mark_subtask_status(n.id, "ready")
        tk.mark_subtask_status(n.id, "in_progress")
        tk.mark_subtask_status(n.id, "completed")
    assert b.id in runnable_ids or b.id in tk.resume(t.id).completed_subtask_ids
