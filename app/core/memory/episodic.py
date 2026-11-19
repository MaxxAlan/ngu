"""Episodic memory: what happened, projected from the Phase 1 event log.

``record`` appends to ``events`` (tasks.db) and mirrors a memory row.
``sync_from_kernel`` backfills any events that were written by the task
kernel directly (status changes, checkpoints, tool effects).
"""

from __future__ import annotations

import json
import logging
import sqlite3

from app import config
from app.core.memory import sqlite_store as store
from app.core.memory.types import MemoryEntry, make_source, new_id
from app.core.memory.writer import MemoryWriter

log = logging.getLogger("local-agents.memory.episodic")


def _format_event(kind: str, payload: dict, created_at: str) -> str:
    body = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    return f"event {kind} at {created_at}: {body}"


class EpisodicMemory:
    def __init__(self, writer: MemoryWriter | None = None) -> None:
        self.writer = writer or MemoryWriter()

    def record(
        self,
        task_id: str,
        kind: str,
        payload: dict | None = None,
        subtask_id: str | None = None,
        *,
        confidence: float = 1.0,
        verified_by: str = "event_log",
    ) -> MemoryEntry:
        """Append an observed event. Observed events are facts, not guesses."""
        if not (task_id or "").strip():
            raise ValueError("task_id is required")
        if not (kind or "").strip():
            raise ValueError("kind is required")
        payload = payload or {}
        event_id = _append_kernel_event(task_id, subtask_id, kind, payload)
        entry_id = f"evt-{event_id}" if event_id else new_id()
        existing = store.get_entry(entry_id)
        if existing is not None:
            return existing
        created = _event_timestamp(event_id) or make_source("task:x")["timestamp"]
        return self.writer.write(
            MemoryEntry(
                id=entry_id,
                type="episodic",
                content=_format_event(kind, payload, created),
                source_json=make_source(
                    f"task:{task_id}",
                    offset=0,
                    timestamp=created,
                    kind=kind,
                    event_id=event_id,
                ),
                confidence=confidence,
                created_at=created,
                key=f"event:{kind}:{entry_id}",
                tags=f"episodic {kind}",
                task_id=task_id,
                subtask_id=subtask_id,
                verified_by=verified_by,
            )
        )

    def sync_from_kernel(self, task_id: str) -> int:
        """Copy kernel ``events`` rows into ``memory_entries`` (idempotent)."""
        copied = 0
        for event in _read_kernel_events(task_id):
            entry_id = f"evt-{event['id']}"
            if store.get_entry(entry_id) is not None:
                continue
            try:
                payload = json.loads(event["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {"raw": event["payload_json"]}
            if not isinstance(payload, dict):
                payload = {"value": payload}
            created = event["created_at"]
            self.writer.write(
                MemoryEntry(
                    id=entry_id,
                    type="episodic",
                    content=_format_event(event["kind"], payload, created),
                    source_json=make_source(
                        f"task:{task_id}",
                        offset=0,
                        timestamp=created,
                        kind=event["kind"],
                        event_id=event["id"],
                    ),
                    confidence=1.0,
                    created_at=created,
                    key=f"event:{event['kind']}:{entry_id}",
                    tags=f"episodic {event['kind']}",
                    task_id=task_id,
                    subtask_id=event["subtask_id"],
                    verified_by="event_log",
                )
            )
            copied += 1
        return copied


def _append_kernel_event(
    task_id: str,
    subtask_id: str | None,
    kind: str,
    payload: dict,
) -> str | None:
    try:
        from app.core.task_kernel import record_event

        return record_event(task_id, subtask_id, kind, payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not append kernel event (%s); storing episodic row only", exc)
        return None


def _event_timestamp(event_id: str | None) -> str | None:
    if not event_id or not config.DB_PATH.exists():
        return None
    con = sqlite3.connect(str(config.DB_PATH))
    try:
        row = con.execute("SELECT created_at FROM events WHERE id = ?", (event_id,)).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def _read_kernel_events(task_id: str) -> list[sqlite3.Row]:
    if not config.DB_PATH.exists():
        return []
    try:
        from app.core.task_kernel import ensure_schema
    except Exception as exc:  # noqa: BLE001
        log.warning("task kernel unavailable (%s)", exc)
        return []
    con = sqlite3.connect(str(config.DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        ensure_schema(con)
        return list(
            con.execute(
                """
                SELECT id, task_id, subtask_id, kind, payload_json, created_at
                FROM events WHERE task_id = ? ORDER BY created_at
                """,
                (task_id,),
            ).fetchall()
        )
    except sqlite3.OperationalError as exc:
        log.warning("could not read events (%s)", exc)
        return []
    finally:
        con.close()
