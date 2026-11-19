"""Procedural memory: reusable procedures with success and failure counts."""

from __future__ import annotations

import re

from app.core.memory import sqlite_store as store
from app.core.memory.types import MemoryEntry, make_source, new_id, now_iso
from app.core.memory.writer import MemoryWriter

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    cleaned = _SLUG.sub("-", name.strip().lower()).strip("-")
    return cleaned or "procedure"


class ProceduralMemory:
    def __init__(self, writer: MemoryWriter | None = None) -> None:
        self.writer = writer or MemoryWriter()

    def add(
        self,
        name: str,
        steps: list[str],
        *,
        source: dict | None = None,
        confidence: float = 1.0,
        verified_by: str = "user",
        key: str | None = None,
    ) -> MemoryEntry:
        if not (name or "").strip():
            raise ValueError("name is required")
        cleaned = [step.strip() for step in steps if step and step.strip()]
        if not cleaned:
            raise ValueError("steps are required")
        body = "\n".join(f"{index}. {step}" for index, step in enumerate(cleaned, start=1))
        content = f"{name.strip()}\n{body}"
        return self.writer.write(
            MemoryEntry(
                id=new_id(),
                type="procedural",
                content=content,
                source_json=source or make_source(f"tool:procedure:{_slug(name)}", offset=0),
                confidence=confidence,
                created_at=now_iso(),
                key=key or f"procedure:{_slug(name)}",
                tags="procedure",
                verified_by=verified_by,
                success_count=0,
                failure_count=0,
            )
        )

    def record_outcome(self, entry_id: str, *, success: bool) -> MemoryEntry:
        current = store.get_entry(entry_id)
        if current is None:
            raise KeyError(f"memory entry not found: {entry_id}")
        if current.type != "procedural":
            raise ValueError(f"entry {entry_id} is {current.type}, not procedural")
        success_count = current.success_count + (1 if success else 0)
        failure_count = current.failure_count + (0 if success else 1)
        verified_at = now_iso()
        with store.connect() as con:
            store.set_counts(con, entry_id, success_count, failure_count, verified_at)
        updated = store.get_entry(entry_id)
        if updated is None:
            raise KeyError(entry_id)
        return updated

    @staticmethod
    def success_rate(entry: MemoryEntry) -> float:
        total = entry.success_count + entry.failure_count
        if total <= 0:
            return 0.0
        return entry.success_count / total
