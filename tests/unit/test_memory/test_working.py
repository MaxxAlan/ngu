"""Working memory is scoped to a subtask and cleared on completion."""

from __future__ import annotations

from app.core import task_kernel as tk
from app.core.memory.retriever import retrieve
from app.core.memory.working import WorkingMemory


def test_put_get_and_hide_from_other_subtasks(mem_env):
    task = tk.create_task("scratch")
    sub = tk.add_subtask(task.id, None, "draft", status="ready")
    other = tk.add_subtask(task.id, None, "other", status="ready")
    wm = WorkingMemory()
    saved = wm.put(sub.id, "uniquezebra working note about the draft", task_id=task.id)
    assert saved.type == "working"
    assert saved.subtask_id == sub.id
    assert wm.get(sub.id)[0].content.startswith("uniquezebra")
    assert wm.get(other.id) == []

    hidden = retrieve("uniquezebra", None, None, 500)
    assert hidden == []
    visible = retrieve("uniquezebra", task.id, sub.id, 500)
    assert any(hit.id == saved.id for hit in visible)


def test_cleared_when_subtask_completes(mem_env):
    task = tk.create_task("finish step")
    sub = tk.add_subtask(task.id, None, "do it", status="ready")
    WorkingMemory().put(sub.id, "ephemeral scratchpad token", task_id=task.id)
    tk.mark_subtask_status(sub.id, "in_progress")
    assert WorkingMemory().get(sub.id)
    tk.mark_subtask_status(sub.id, "completed")
    assert WorkingMemory().get(sub.id) == []
