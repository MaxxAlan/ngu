"""Working memory: the current subtask's scratch context.

Rows are real SQLite entries so they participate in retrieval for that
subtask, and they are deleted when the subtask completes.
"""

from __future__ import annotations

from app.core.memory import sqlite_store as store
from app.core.memory.types import MemoryEntry, make_source, new_id, now_iso
from app.core.memory.writer import MemoryWriter


class WorkingMemory:
    def __init__(self, writer: MemoryWriter | None = None) -> None:
        self.writer = writer or MemoryWriter()

    def put(
        self,
        subtask_id: str,
        content: str,
        *,
        task_id: str | None = None,
        source: dict | None = None,
        key: str | None = None,
    ) -> MemoryEntry:
        if not (subtask_id or "").strip():
            raise ValueError("subtask_id is required")
        if not (content or "").strip():
            raise ValueError("content is required")
        origin = f"task:{task_id}" if task_id else f"task:subtask:{subtask_id}"
        entry = MemoryEntry(
            id=new_id(),
            type="working",
            content=content.strip(),
            source_json=source or make_source(origin, offset=0),
            confidence=1.0,
            created_at=now_iso(),
            key=key,
            tags="working",
            task_id=task_id,
            subtask_id=subtask_id,
            verified_by="working_memory",
        )
        return self.writer.write(entry)

    def get(self, subtask_id: str) -> list[MemoryEntry]:
        return store.list_working(subtask_id)

    def clear(self, subtask_id: str) -> int:
        """Drop every working entry for ``subtask_id`` (called on completion)."""
        if not subtask_id:
            return 0
        with store.connect() as con:
            return store.delete_working(con, subtask_id)
