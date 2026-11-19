"""Episodic memory is projected from the Phase 1 events table."""

from __future__ import annotations

from app.core import task_kernel as tk
from app.core.memory.episodic import EpisodicMemory
from app.core.memory.retriever import retrieve


def test_record_appends_event_and_is_searchable(mem_env):
    task = tk.create_task("watch tools")
    sub = tk.add_subtask(task.id, None, "read", status="ready")
    saved = EpisodicMemory().record(
        task.id,
        "tool_call",
        {"name": "read_file", "path": "notes.txt"},
        sub.id,
    )
    assert saved.type == "episodic"
    assert saved.confidence == 1.0
    assert "read_file" in saved.content
    assert saved.source_json["origin"] == f"task:{task.id}"
    assert saved.task_id == task.id

    copied = EpisodicMemory().sync_from_kernel(task.id)
    # task_created / subtask_added were not mirrored yet; the tool_call row already exists.
    assert copied >= 1
    again = EpisodicMemory().sync_from_kernel(task.id)
    assert again == 0

    hits = retrieve("read_file notes.txt", task.id, sub.id, 800)
    assert any(hit.id == saved.id for hit in hits)
    assert hits[0].source_json.get("timestamp")
