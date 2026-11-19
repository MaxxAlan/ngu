"""Write policy for permanent memory.

A model guess is not a fact. ``write`` rejects entries whose confidence is
below 0.7 unless ``verified_by`` names who checked them. Superseded and
expired rows stay in the table so provenance is recoverable.
"""

from __future__ import annotations

from app.core.memory import sqlite_store as store
from app.core.memory.embeddings import safe_embed
from app.core.memory.types import MEMORY_TYPES, MIN_CONFIDENCE, MemoryEntry, new_id, now_iso


class MemoryWriteError(ValueError):
    """Raised when a write violates the confidence / provenance policy."""


class MemoryWriter:
    def write(self, entry: MemoryEntry) -> MemoryEntry:
        verified = (entry.verified_by or "").strip()
        if entry.confidence < MIN_CONFIDENCE and not verified:
            raise MemoryWriteError(
                "refusing permanent memory write: confidence "
                f"{entry.confidence:.2f} < {MIN_CONFIDENCE} and verified_by is empty. "
                "Model guesses are not stored as facts."
            )
        if entry.type not in MEMORY_TYPES:
            raise MemoryWriteError(f"unknown memory type: {entry.type!r}")
        if not (entry.content or "").strip():
            raise MemoryWriteError("content must not be empty")
        source = _require_provenance(dict(entry.source_json or {}))
        created = entry.created_at or now_iso()
        verified_at = entry.last_verified_at
        if verified_at is None and (verified or entry.confidence >= MIN_CONFIDENCE):
            verified_at = str(source.get("timestamp") or created)
        saved = MemoryEntry(
            id=entry.id or new_id(),
            type=entry.type,
            content=entry.content.strip(),
            source_json=source,
            confidence=float(entry.confidence),
            created_at=created,
            last_verified_at=verified_at,
            superseded_by=entry.superseded_by,
            embedding=entry.embedding if entry.embedding is not None else safe_embed(entry.content),
            key=entry.key,
            tags=entry.tags or "",
            task_id=entry.task_id,
            subtask_id=entry.subtask_id,
            verified_by=verified or None,
            expires_at=entry.expires_at,
            success_count=int(entry.success_count),
            failure_count=int(entry.failure_count),
        )
        with store.connect() as con:
            store.insert_entry(con, saved)
        saved.embedding = None
        return saved

    def upsert_key(
        self,
        *,
        key: str,
        content: str,
        tags: str = "",
        type: str = "episodic",
        confidence: float = 1.0,
        verified_by: str | None = None,
        source: dict,
        task_id: str | None = None,
        subtask_id: str | None = None,
    ) -> MemoryEntry:
        """Insert ``key`` and supersede any previous active row with that key."""
        saved = self.write(
            MemoryEntry(
                id=new_id(),
                type=type,
                content=content,
                source_json=source,
                confidence=confidence,
                created_at=now_iso(),
                key=key,
                tags=tags,
                task_id=task_id,
                subtask_id=subtask_id,
                verified_by=verified_by,
            )
        )
        for previous in store.active_by_key(key):
            if previous.id != saved.id:
                self.supersede(previous.id, saved.id)
        return saved

    def supersede(self, entry_id: str, new_entry_id: str) -> None:
        """Keep ``entry_id`` but mark it replaced by ``new_entry_id``."""
        if entry_id == new_entry_id:
            raise MemoryWriteError("cannot supersede an entry with itself")
        if store.get_entry(entry_id) is None:
            raise KeyError(f"memory entry not found: {entry_id}")
        if store.get_entry(new_entry_id) is None:
            raise KeyError(f"memory entry not found: {new_entry_id}")
        with store.connect() as con:
            store.set_superseded(con, entry_id, new_entry_id)
            store.add_link(con, entry_id, new_entry_id, "superseded_by", 1.0)

    def expire(self, entry_id: str, *, at: str | None = None) -> MemoryEntry:
        """Hide a time-sensitive fact from retrieval without deleting it."""
        if store.get_entry(entry_id) is None:
            raise KeyError(f"memory entry not found: {entry_id}")
        when = at or now_iso()
        with store.connect() as con:
            store.set_expiry(con, entry_id, when)
        updated = store.get_entry(entry_id)
        if updated is None:
            raise KeyError(entry_id)
        return updated

    def link(self, from_id: str, to_id: str, relation: str, weight: float = 1.0) -> None:
        if not (relation or "").strip():
            raise MemoryWriteError("relation is required")
        if weight < 0:
            raise MemoryWriteError("weight must be >= 0")
        if store.get_entry(from_id) is None or store.get_entry(to_id) is None:
            raise KeyError("link endpoints must exist")
        with store.connect() as con:
            store.add_link(con, from_id, to_id, relation.strip(), float(weight))


def _require_provenance(source: dict) -> dict:
    if not isinstance(source, dict):
        raise MemoryWriteError("source_json must be an object")
    origin = source.get("origin")
    if not isinstance(origin, str) or not origin.strip():
        raise MemoryWriteError("source_json.origin is required (file/url/tool/task)")
    if "offset" not in source:
        raise MemoryWriteError("source_json.offset is required")
    timestamp = source.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise MemoryWriteError("source_json.timestamp is required")
    source["origin"] = origin.strip()
    source["timestamp"] = timestamp.strip()
    return source
