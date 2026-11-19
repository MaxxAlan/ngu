"""Semantic memory: stable facts, entities, and relationships."""

from __future__ import annotations

import re

from app.core.memory import sqlite_store as store
from app.core.memory.types import MemoryEntry, make_source, new_id, now_iso
from app.core.memory.writer import MemoryWriter

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    cleaned = _SLUG.sub("-", name.strip().lower()).strip("-")
    return cleaned or "entity"


class SemanticMemory:
    def __init__(self, writer: MemoryWriter | None = None) -> None:
        self.writer = writer or MemoryWriter()

    def add_fact(
        self,
        content: str,
        *,
        source: dict | None = None,
        confidence: float = 0.9,
        verified_by: str | None = None,
        entities: list[str] | None = None,
        key: str | None = None,
        tags: str = "",
    ) -> MemoryEntry:
        """Store a fact. Low-confidence unverified guesses are rejected."""
        origin_source = source or make_source("tool:semantic", offset=0)
        saved = self.writer.write(
            MemoryEntry(
                id=new_id(),
                type="semantic",
                content=content,
                source_json=origin_source,
                confidence=confidence,
                created_at=now_iso(),
                key=key,
                tags=tags or "fact",
                verified_by=verified_by,
            )
        )
        for name in entities or []:
            if not (name or "").strip():
                continue
            entity = self.ensure_entity(name.strip(), source=origin_source, verified_by=verified_by)
            self.writer.link(saved.id, entity.id, "mentions", 1.0)
        return saved

    def ensure_entity(
        self,
        name: str,
        *,
        source: dict | None = None,
        verified_by: str | None = None,
    ) -> MemoryEntry:
        key = f"entity:{_slug(name)}"
        existing = [row for row in store.active_by_key(key) if not row.expires_at]
        if existing:
            return existing[0]
        return self.writer.write(
            MemoryEntry(
                id=new_id(),
                type="semantic",
                content=f"entity {name}",
                source_json=source or make_source("tool:semantic", offset=0),
                confidence=1.0,
                created_at=now_iso(),
                key=key,
                tags="entity",
                verified_by=verified_by or "semantic",
            )
        )

    def link(self, from_id: str, to_id: str, relation: str, weight: float = 1.0) -> None:
        self.writer.link(from_id, to_id, relation, weight)
