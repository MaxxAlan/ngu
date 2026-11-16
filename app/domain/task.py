"""Task aggregate and status vocabulary."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

TaskStatus = Literal["draft", "queued", "running", "done", "failed", "cancelled"]
TaskSource = Literal["ui", "mcp", "cli", "api"]

TASK_STATUSES: tuple[str, ...] = (
    "draft",
    "queued",
    "running",
    "done",
    "failed",
    "cancelled",
)


class TaskRecord(TypedDict, total=False):
    """Canonical task shape shared by persistence, API, and MCP adapters."""

    id: str
    title: str
    prompt: str
    agent: str
    model: str
    status: str
    source: str
    result: str
    error: str
    attachments: Any  # JSON list or serialized string depending on boundary
    steps: Any
    messages: Any
    resume_attempts: int
    created_at: str
    started_at: str | None
    finished_at: str | None
