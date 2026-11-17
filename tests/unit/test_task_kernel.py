"""Unit tests for the Phase 1 durable task kernel."""

from __future__ import annotations

import os
import tempfile

import pytest

# Isolate DB before importing kernel / config.
_TMP = tempfile.mkdtemp(prefix="ngu-kernel-")
os.environ["LA_DATA_DIR"] = _TMP
os.environ["LA_TASK_KERNEL"] = "on"

from app.core import task_kernel as tk  # noqa: E402
from app.core.task_kernel import IllegalTransition  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """Each test gets an isolated SQLite file."""
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    monkeypatch.setenv("LA_DATA_DIR", str(db_dir))
    # Reload config paths used by the kernel.
    import app.config as cfg

    monkeypatch.setattr(cfg, "DATA_DIR", db_dir)
    monkeypatch.setattr(cfg, "DB_PATH", db_dir / "tasks.db")
    monkeypatch.setattr(cfg, "TASK_KERNEL", True)
    tk.ensure_schema()
    yield


def test_create_task():
    t = tk.create_task("Ship the feature", constraints={"lang": "py"}, acceptance={"tests": "pass"})
    assert t.id
    assert t.objective == "Ship the feature"
    assert t.status == "queued"
    assert t.constraints["lang"] == "py"
    loaded = tk.get_task(t.id)
    assert loaded is not None
    assert loaded.objective == t.objective


def test_add_subtask_and_next_runnable():
    t = tk.create_task("Build X")
    s1 = tk.add_subtask(t.id, None, "write code", status="ready")
    s2 = tk.add_subtask(t.id, s1.id, "run tests", depends_on=[s1.id], status="pending")
    nxt = tk.next_runnable_subtask(t.id)
    assert nxt is not None
    assert nxt.id == s1.id
    tk.mark_subtask_status(s1.id, "in_progress")
    tk.mark_subtask_status(s1.id, "completed")
    nxt2 = tk.next_runnable_subtask(t.id)
    assert nxt2 is not None
    assert nxt2.id == s2.id


def test_illegal_transition_rejected():
    t = tk.create_task("T")
    s = tk.add_subtask(t.id, None, "step", status="ready")
    tk.mark_subtask_status(s.id, "in_progress")
    tk.mark_subtask_status(s.id, "completed")
    with pytest.raises(IllegalTransition):
        tk.mark_subtask_status(s.id, "in_progress")
    with pytest.raises(IllegalTransition):
        tk.mark_subtask_status(s.id, "pending")


def test_resume_from_mid_run():
    t = tk.create_task("Long task", status="running")
    s1 = tk.add_subtask(t.id, None, "step-1", status="ready")
    s2 = tk.add_subtask(t.id, s1.id, "step-2", depends_on=[s1.id], status="pending")
    s3 = tk.add_subtask(t.id, s2.id, "step-3", depends_on=[s2.id], status="pending")
    tk.mark_subtask_status(s1.id, "in_progress")
    tk.mark_subtask_status(s1.id, "completed")
    tk.mark_subtask_status(s2.id, "in_progress")
    tk.checkpoint(t.id)

    # Simulate crash: close connections by just calling resume on a "reopened" view.
    state = tk.resume(t.id)
    assert state.task.id == t.id
    assert s1.id in state.completed_subtask_ids
    assert s2.id not in state.completed_subtask_ids
    assert state.next_subtask is None or state.next_subtask.id == s2.id or state.next_subtask.id == s3.id
    # s2 is in_progress, so next runnable among pending/ready should be none until s2 finishes;
    # after completing s2, s3 becomes runnable.
    tk.mark_subtask_status(s2.id, "completed")
    nxt = tk.next_runnable_subtask(t.id)
    assert nxt is not None
    assert nxt.id == s3.id
    assert s1.id in tk.resume(t.id).completed_subtask_ids
    assert s2.id in tk.resume(t.id).completed_subtask_ids


def test_idempotent_tool_effect():
    key = "taskA:sub1:write_file"
    args = {"path": "out.txt", "content": "hello"}
    assert tk.lookup_tool_effect(key, "write_file", args) is None
    tk.store_tool_effect(key, "write_file", args, "wrote 5 bytes")
    cached = tk.lookup_tool_effect(key, "write_file", args)
    assert cached == "wrote 5 bytes"
    # Different args => miss
    assert tk.lookup_tool_effect(key, "write_file", {"path": "other.txt", "content": "x"}) is None


def test_record_event_and_artifact():
    t = tk.create_task("E")
    eid = tk.record_event(t.id, None, "note", {"msg": "hi"})
    assert eid
    aid = tk.record_artifact(t.id, "build/out.bin", sha256="abc")
    assert aid


def test_schema_idempotent():
    tk.ensure_schema()
    tk.ensure_schema()  # second call must not raise
    t = tk.create_task("again")
    assert t.id
