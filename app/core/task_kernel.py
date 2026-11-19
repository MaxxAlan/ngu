"""Durable Task Kernel — task graph outside the model transcript.

All mutations run inside a single SQLite IMMEDIATE transaction.
Schema migrations are idempotent so a fresh DB and an existing DB both start cleanly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app import config

_log = logging.getLogger("local-agents.kernel")

# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------

TASK_STATUSES = ("draft", "queued", "running", "done", "failed", "cancelled")
SUBTASK_STATUSES = ("pending", "ready", "in_progress", "completed", "failed", "blocked", "cancelled")

# Legal subtask transitions (from -> frozenset of allowed next)
_SUBTASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"ready", "in_progress", "cancelled", "blocked"}),
    "ready": frozenset({"in_progress", "cancelled", "blocked"}),
    "in_progress": frozenset({"completed", "failed", "blocked", "cancelled"}),
    "completed": frozenset(),  # terminal
    "failed": frozenset({"pending", "ready", "in_progress"}),  # retry allowed
    "blocked": frozenset({"ready", "pending", "cancelled"}),
    "cancelled": frozenset(),  # terminal
}


class IllegalTransition(ValueError):
    """Raised when mark_subtask_status requests a disallowed status change."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Task:
    id: str
    objective: str
    constraints: dict[str, Any] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Subtask:
    id: str
    task_id: str
    parent_id: str | None
    description: str
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)
    acceptance: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskState:
    """Snapshot returned by resume()."""

    task: Task
    subtasks: list[Subtask]
    last_checkpoint_at: str | None
    completed_subtask_ids: list[str]
    next_subtask: Subtask | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "subtasks": [s.to_dict() for s in self.subtasks],
            "last_checkpoint_at": self.last_checkpoint_at,
            "completed_subtask_ids": list(self.completed_subtask_ids),
            "next_subtask": self.next_subtask.to_dict() if self.next_subtask else None,
        }


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(config.DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _uid() -> str:
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Schema (idempotent)
# ---------------------------------------------------------------------------

_KERNEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS kernel_tasks (
    id                       TEXT PRIMARY KEY,
    objective                TEXT NOT NULL,
    constraints_json         TEXT NOT NULL DEFAULT '{}',
    acceptance_criteria_json TEXT NOT NULL DEFAULT '{}',
    status                   TEXT NOT NULL DEFAULT 'queued',
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kernel_tasks_status ON kernel_tasks(status, created_at);

CREATE TABLE IF NOT EXISTS subtasks (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    parent_id       TEXT,
    description     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    acceptance_json TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES kernel_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_subtasks_task ON subtasks(task_id, status);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    subtask_id  TEXT,
    kind        TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES kernel_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    subtask_id  TEXT,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES kernel_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);

CREATE TABLE IF NOT EXISTS tool_effects (
    idempotency_key TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    args_hash       TEXT NOT NULL,
    result_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (idempotency_key, tool_name, args_hash)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    task_id     TEXT PRIMARY KEY,
    marker      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES kernel_tasks(id)
);
"""


def ensure_schema(con: sqlite3.Connection | None = None) -> None:
    """Idempotent schema bootstrap. Safe on fresh and existing DBs."""
    own = con is None
    if own:
        con = _connect()
    assert con is not None
    try:
        con.executescript(_KERNEL_SCHEMA)
        # Light column migrations for future-proofing (no-op if already present)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(kernel_tasks)").fetchall()}
        if "legacy_task_id" not in cols:
            con.execute(
                "ALTER TABLE kernel_tasks ADD COLUMN legacy_task_id TEXT NOT NULL DEFAULT ''"
            )
        con.commit()
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_task(
    objective: str,
    constraints: dict[str, Any] | None = None,
    acceptance: dict[str, Any] | None = None,
    *,
    task_id: str | None = None,
    legacy_task_id: str = "",
    status: str = "queued",
) -> Task:
    """Create a durable task record. Returns the Task."""
    if not (objective or "").strip():
        raise ValueError("objective is required")
    if status not in TASK_STATUSES:
        raise ValueError(f"invalid task status: {status}")

    tid = task_id or _uid()
    ts = _now()
    constraints = constraints or {}
    acceptance = acceptance or {}
    task = Task(
        id=tid,
        objective=objective.strip(),
        constraints=constraints,
        acceptance=acceptance,
        status=status,
        created_at=ts,
        updated_at=ts,
    )
    with _connect() as con:
        ensure_schema(con)
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT INTO kernel_tasks "
            "(id, objective, constraints_json, acceptance_criteria_json, status, "
            " created_at, updated_at, legacy_task_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.id,
                task.objective,
                json.dumps(constraints, ensure_ascii=False),
                json.dumps(acceptance, ensure_ascii=False),
                task.status,
                task.created_at,
                task.updated_at,
                legacy_task_id or "",
            ),
        )
        con.execute(
            "INSERT INTO events (id, task_id, subtask_id, kind, payload_json, created_at) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (
                _uid(),
                task.id,
                "task_created",
                json.dumps({"objective": task.objective}, ensure_ascii=False),
                ts,
            ),
        )
        con.commit()
    return task


def add_subtask(
    task_id: str,
    parent_id: str | None,
    description: str,
    depends_on: list[str] | None = None,
    acceptance: dict[str, Any] | None = None,
    *,
    status: str = "pending",
    subtask_id: str | None = None,
) -> Subtask:
    """Add a subtask node to the graph."""
    if not (description or "").strip():
        raise ValueError("description is required")
    if status not in SUBTASK_STATUSES:
        raise ValueError(f"invalid subtask status: {status}")

    sid = subtask_id or _uid()
    ts = _now()
    depends_on = list(depends_on or [])
    acceptance = acceptance or {}
    sub = Subtask(
        id=sid,
        task_id=task_id,
        parent_id=parent_id,
        description=description.strip(),
        status=status,
        depends_on=depends_on,
        acceptance=acceptance,
        created_at=ts,
        updated_at=ts,
    )
    with _connect() as con:
        ensure_schema(con)
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT id FROM kernel_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        con.execute(
            "INSERT INTO subtasks "
            "(id, task_id, parent_id, description, status, depends_on_json, "
            " acceptance_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sub.id,
                sub.task_id,
                sub.parent_id,
                sub.description,
                sub.status,
                json.dumps(depends_on),
                json.dumps(acceptance, ensure_ascii=False),
                sub.created_at,
                sub.updated_at,
            ),
        )
        con.execute(
            "UPDATE kernel_tasks SET updated_at = ? WHERE id = ?",
            (ts, task_id),
        )
        con.execute(
            "INSERT INTO events (id, task_id, subtask_id, kind, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _uid(),
                task_id,
                sub.id,
                "subtask_added",
                json.dumps({"description": sub.description, "status": status}, ensure_ascii=False),
                ts,
            ),
        )
        con.commit()
    return sub


def mark_subtask_status(subtask_id: str, status: str) -> Subtask:
    """Transition a subtask to a new status. Raises IllegalTransition if illegal."""
    if status not in SUBTASK_STATUSES:
        raise ValueError(f"invalid subtask status: {status}")

    with _connect() as con:
        ensure_schema(con)
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM subtasks WHERE id = ?", (subtask_id,)).fetchone()
        if row is None:
            raise KeyError(f"subtask not found: {subtask_id}")
        current = row["status"]
        allowed = _SUBTASK_TRANSITIONS.get(current, frozenset())
        if status not in allowed and status != current:
            raise IllegalTransition(
                f"cannot transition subtask {subtask_id} from {current!r} to {status!r}"
            )
        ts = _now()
        con.execute(
            "UPDATE subtasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, ts, subtask_id),
        )
        con.execute(
            "UPDATE kernel_tasks SET updated_at = ? WHERE id = ?",
            (ts, row["task_id"]),
        )
        con.execute(
            "INSERT INTO events (id, task_id, subtask_id, kind, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _uid(),
                row["task_id"],
                subtask_id,
                "subtask_status",
                json.dumps({"from": current, "to": status}, ensure_ascii=False),
                ts,
            ),
        )
        con.commit()
        row2 = con.execute("SELECT * FROM subtasks WHERE id = ?", (subtask_id,)).fetchone()
    if status == "completed":
        _clear_working_memory(subtask_id)
    return _row_to_subtask(row2)


def _clear_working_memory(subtask_id: str) -> None:
    """Ephemeral working memory dies with the subtask. Failures must not roll back status."""
    try:
        from app.core.memory.working import WorkingMemory

        WorkingMemory().clear(subtask_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning("working memory clear failed for %s: %s", subtask_id, exc)


def next_runnable_subtask(task_id: str) -> Subtask | None:
    """Return the next subtask that is ready to run (deps satisfied, status pending/ready)."""
    with _connect() as con:
        ensure_schema(con)
        rows = con.execute(
            "SELECT * FROM subtasks WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
    if not rows:
        return None

    completed = {r["id"] for r in rows if r["status"] == "completed"}

    for r in rows:
        if r["status"] not in ("pending", "ready"):
            continue
        deps = json.loads(r["depends_on_json"] or "[]")
        if all(d in completed for d in deps):
            return _row_to_subtask(r)
    return None


def record_event(
    task_id: str,
    subtask_id: str | None,
    kind: str,
    payload: dict[str, Any] | None = None,
) -> str:
    """Append an event to the immutable event log. Returns event id."""
    eid = _uid()
    ts = _now()
    with _connect() as con:
        ensure_schema(con)
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT INTO events (id, task_id, subtask_id, kind, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                eid,
                task_id,
                subtask_id,
                kind,
                json.dumps(payload or {}, ensure_ascii=False),
                ts,
            ),
        )
        con.execute(
            "UPDATE kernel_tasks SET updated_at = ? WHERE id = ?",
            (ts, task_id),
        )
        con.commit()
    return eid


def checkpoint(task_id: str) -> str:
    """Atomically persist a consistent snapshot marker. Returns marker id."""
    marker = _uid()
    ts = _now()
    with _connect() as con:
        ensure_schema(con)
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT id FROM kernel_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        con.execute(
            "INSERT INTO checkpoints (task_id, marker, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET marker = excluded.marker, "
            "created_at = excluded.created_at",
            (task_id, marker, ts),
        )
        con.execute(
            "INSERT INTO events (id, task_id, subtask_id, kind, payload_json, created_at) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (
                _uid(),
                task_id,
                "checkpoint",
                json.dumps({"marker": marker}, ensure_ascii=False),
                ts,
            ),
        )
        con.execute(
            "UPDATE kernel_tasks SET updated_at = ? WHERE id = ?",
            (ts, task_id),
        )
        con.commit()
    return marker


def resume(task_id: str) -> TaskState:
    """Return the last valid state after any crash. Does not re-execute completed work."""
    with _connect() as con:
        ensure_schema(con)
        trow = con.execute("SELECT * FROM kernel_tasks WHERE id = ?", (task_id,)).fetchone()
        if trow is None:
            raise KeyError(f"task not found: {task_id}")
        srows = con.execute(
            "SELECT * FROM subtasks WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        cp = con.execute(
            "SELECT marker, created_at FROM checkpoints WHERE task_id = ?",
            (task_id,),
        ).fetchone()

    task = _row_to_task(trow)
    subtasks = [_row_to_subtask(r) for r in srows]
    completed = [s.id for s in subtasks if s.status == "completed"]
    nxt = next_runnable_subtask(task_id)
    return TaskState(
        task=task,
        subtasks=subtasks,
        last_checkpoint_at=cp["created_at"] if cp else None,
        completed_subtask_ids=completed,
        next_subtask=nxt,
    )


def get_task(task_id: str) -> Task | None:
    with _connect() as con:
        ensure_schema(con)
        row = con.execute("SELECT * FROM kernel_tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def set_task_status(task_id: str, status: str) -> Task:
    if status not in TASK_STATUSES:
        raise ValueError(f"invalid task status: {status}")
    ts = _now()
    with _connect() as con:
        ensure_schema(con)
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "UPDATE kernel_tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, ts, task_id),
        )
        con.execute(
            "INSERT INTO events (id, task_id, subtask_id, kind, payload_json, created_at) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (
                _uid(),
                task_id,
                "task_status",
                json.dumps({"status": status}, ensure_ascii=False),
                ts,
            ),
        )
        con.commit()
        row = con.execute("SELECT * FROM kernel_tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row)


def record_artifact(
    task_id: str,
    path: str,
    *,
    subtask_id: str | None = None,
    sha256: str = "",
) -> str:
    aid = _uid()
    ts = _now()
    with _connect() as con:
        ensure_schema(con)
        con.execute(
            "INSERT INTO artifacts (id, task_id, subtask_id, path, sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (aid, task_id, subtask_id, path, sha256, ts),
        )
        con.commit()
    return aid


# ---------------------------------------------------------------------------
# Tool-effect idempotency
# ---------------------------------------------------------------------------


def args_hash(args: dict[str, Any]) -> str:
    payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def lookup_tool_effect(
    idempotency_key: str,
    tool_name: str,
    args: dict[str, Any],
) -> str | None:
    """Return cached result if this exact effect was already applied."""
    h = args_hash(args)
    with _connect() as con:
        ensure_schema(con)
        row = con.execute(
            "SELECT result_json FROM tool_effects "
            "WHERE idempotency_key = ? AND tool_name = ? AND args_hash = ?",
            (idempotency_key, tool_name, h),
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["result_json"])
    except (TypeError, json.JSONDecodeError):
        return row["result_json"]


def store_tool_effect(
    idempotency_key: str,
    tool_name: str,
    args: dict[str, Any],
    result: str,
) -> None:
    h = args_hash(args)
    ts = _now()
    with _connect() as con:
        ensure_schema(con)
        con.execute(
            "INSERT INTO tool_effects (idempotency_key, tool_name, args_hash, result_json, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(idempotency_key, tool_name, args_hash) DO NOTHING",
            (idempotency_key, tool_name, h, json.dumps(result, ensure_ascii=False), ts),
        )
        con.commit()


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        objective=row["objective"],
        constraints=json.loads(row["constraints_json"] or "{}"),
        acceptance=json.loads(row["acceptance_criteria_json"] or "{}"),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_subtask(row: sqlite3.Row) -> Subtask:
    return Subtask(
        id=row["id"],
        task_id=row["task_id"],
        parent_id=row["parent_id"],
        description=row["description"],
        status=row["status"],
        depends_on=json.loads(row["depends_on_json"] or "[]"),
        acceptance=json.loads(row["acceptance_json"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
