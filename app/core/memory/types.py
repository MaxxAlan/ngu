"""Shared types for the hierarchical memory engine."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MEMORY_TYPES = ("working", "episodic", "semantic", "procedural")
MIN_CONFIDENCE = 0.7


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def make_source(
    origin: str,
    offset: int | str = 0,
    timestamp: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Provenance object stored in ``memory_entries.source_json``.

    ``origin`` is a file path, URL, tool name, or task id (prefixed
    ``file:`` / ``url:`` / ``tool:`` / ``task:``).
    """
    payload: dict[str, Any] = {
        "origin": origin,
        "offset": 0 if offset is None else offset,
        "timestamp": timestamp or now_iso(),
    }
    if extra:
        payload["extra"] = extra
    return payload


@dataclass
class MemoryEntry:
    """One memory row plus retrieval annotations (score is not persisted)."""

    id: str
    type: str
    content: str
    source_json: dict[str, Any]
    confidence: float
    created_at: str
    last_verified_at: str | None = None
    superseded_by: str | None = None
    embedding: list[float] | None = None
    key: str | None = None
    tags: str = ""
    task_id: str | None = None
    subtask_id: str | None = None
    verified_by: str | None = None
    expires_at: str | None = None
    success_count: int = 0
    failure_count: int = 0
    score: float | None = None
    score_parts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "source_json": self.source_json,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "last_verified_at": self.last_verified_at,
            "superseded_by": self.superseded_by,
            "key": self.key,
            "tags": self.tags,
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "verified_by": self.verified_by,
            "expires_at": self.expires_at,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "score": self.score,
            "score_parts": self.score_parts,
        }
