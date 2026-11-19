"""Crash-and-resume with external file modification triggers revalidation."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="ngu-reval-")
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
    monkeypatch.setattr(cfg, "WORKSPACE_DIR", tmp_path / "ws")
    from app.core import task_kernel as tk

    tk.ensure_schema()
    return tk


def test_external_modification_triggers_revalidation(kernel_db, tmp_path, monkeypatch):
    tk = kernel_db
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr("app.config.WORKSPACE_DIR", ws)

    task = tk.create_task("write then protect", status="running")
    sub = tk.add_subtask(task.id, None, "tool:write_file out.txt", status="ready")
    tk.mark_subtask_status(sub.id, "in_progress")

    content = "original payload"
    target = ws / "out.txt"
    target.write_text(content, encoding="utf-8")

    tk.store_tool_effect(
        f"{task.id}:{sub.id}:write_file",
        "write_file",
        {"path": "out.txt", "content": content},
        "ok",
        subtask_id=sub.id,
    )
    # Complete without acceptance criteria (empty → auto-pass)
    tk.mark_subtask_status(sub.id, "completed")
    tk.checkpoint(task.id)

    # Simulate external modification between crash and resume
    target.write_text("TAMPERED BY EXTERNAL PROCESS", encoding="utf-8")

    # Direct revalidate
    result = tk.revalidate(sub.id, workspace=ws)
    assert result["ok"] is False
    assert result["mismatches"]

    # resume() must not treat the subtask as completed
    state = tk.resume(task.id, revalidate_effects=True)
    # After revalidation + promotion, subtask should not be in completed set
    # (either needs_revalidation was set then promoted to ready/pending)
    assert sub.id not in state.completed_subtask_ids
    statuses = {s.id: s.status for s in state.subtasks}
    assert statuses[sub.id] in ("ready", "pending", "needs_revalidation", "in_progress")


def test_unmodified_effect_still_skips(kernel_db, tmp_path, monkeypatch):
    tk = kernel_db
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr("app.config.WORKSPACE_DIR", ws)

    task = tk.create_task("stable file", status="running")
    sub = tk.add_subtask(task.id, None, "tool:write_file stable.txt", status="ready")
    tk.mark_subtask_status(sub.id, "in_progress")

    content = "stable"
    (ws / "stable.txt").write_text(content, encoding="utf-8")
    tk.store_tool_effect(
        f"{task.id}:{sub.id}:write_file",
        "write_file",
        {"path": "stable.txt", "content": content},
        "ok",
        subtask_id=sub.id,
    )
    tk.mark_subtask_status(sub.id, "completed")

    result = tk.revalidate(sub.id, workspace=ws)
    assert result["ok"] is True

    state = tk.resume(task.id, revalidate_effects=True)
    assert sub.id in state.completed_subtask_ids
