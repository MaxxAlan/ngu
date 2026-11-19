"""Prove the agent cannot declare a failed task as done — verifier rejects it."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="ngu-false-complete-")
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
    monkeypatch.setattr(cfg, "VERIFIER_MAX_RETRIES", 0)
    from app.core import task_kernel as tk

    tk.ensure_schema()
    return tk


def test_flawed_deliverable_rejected(kernel_db, tmp_path):
    """Benchmark: function returns 4 but test expects 5 → not marked complete."""
    tk = kernel_db
    ws = tmp_path / "ws"
    ws.mkdir()

    # Deliberately flawed deliverable
    (ws / "answer.py").write_text("def answer():\n    return 4\n", encoding="utf-8")

    acceptance = {
        "criteria": [
            {"type": "file_exists", "path": "answer.py"},
            {
                "type": "test_passes",
                "cmd": "python -c \"from answer import answer; assert answer() == 5\"",
            },
        ]
    }
    task = tk.create_task(
        "Write a function that returns 5",
        acceptance=acceptance,
        status="running",
    )
    sub = tk.add_subtask(
        task.id,
        None,
        "write answer.py",
        status="ready",
        acceptance=acceptance,
    )
    tk.mark_subtask_status(sub.id, "in_progress")

    # Doer claims done — verifier must reject
    ok, evidence = tk.try_complete_subtask(sub.id, workspace=ws)
    assert not ok
    assert "exit=" in evidence or "assert" in evidence.lower() or "fail" in evidence.lower()

    reloaded = tk.resume(task.id, revalidate_effects=False)
    statuses = {s.id: s.status for s in reloaded.subtasks}
    assert statuses[sub.id] != "completed"

    # Task-level gate also rejects
    task_ok, task_ev = tk.try_complete_task(task.id, workspace=ws)
    assert not task_ok
    assert tk.get_task(task.id).status == "failed"


def test_correct_deliverable_accepted(kernel_db, tmp_path):
    tk = kernel_db
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "answer.py").write_text("def answer():\n    return 5\n", encoding="utf-8")

    acceptance = {
        "criteria": [
            {"type": "file_exists", "path": "answer.py"},
            {
                "type": "test_passes",
                "cmd": "python -c \"from answer import answer; assert answer() == 5\"",
            },
        ]
    }
    task = tk.create_task("ok", acceptance=acceptance, status="running")
    sub = tk.add_subtask(task.id, None, "write", status="ready", acceptance=acceptance)
    tk.mark_subtask_status(sub.id, "in_progress")

    ok, _ = tk.try_complete_subtask(sub.id, workspace=ws)
    assert ok
    assert tk.resume(task.id, revalidate_effects=False).subtasks[0].status == "completed"

    task_ok, summary = tk.try_complete_task(task.id, workspace=ws)
    assert task_ok
    assert Path(summary).exists()
    assert tk.get_task(task.id).status == "done"
