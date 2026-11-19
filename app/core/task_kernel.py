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
SUBTASK_STATUSES = (
    "pending",
    "ready",
    "in_progress",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "needs_revalidation",
)

# Legal subtask transitions (from -> frozenset of allowed next)
_SUBTASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"ready", "in_progress", "cancelled", "blocked"}),
    "ready": frozenset({"in_progress", "cancelled", "blocked"}),
    "in_progress": frozenset({"completed", "failed", "blocked", "cancelled", "needs_revalidation"}),
    "completed": frozenset({"needs_revalidation"}),  # external change can reopen
    "failed": frozenset({"pending", "ready", "in_progress"}),  # retry allowed
    "blocked": frozenset({"ready", "pending", "cancelled"}),
    "cancelled": frozenset(),  # terminal
    "needs_revalidation": frozenset({"pending", "ready", "in_progress", "completed", "failed", "blocked"}),
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
    args_json       TEXT NOT NULL DEFAULT '{}',
    subtask_id      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    PRIMARY KEY (idempotency_key, tool_name, args_hash)
);
CREATE INDEX IF NOT EXISTS idx_tool_effects_subtask ON tool_effects(subtask_id);

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
        # Phase 4: tool_effects.args_json / subtask_id for semantic revalidation
        te_cols = {r["name"] for r in con.execute("PRAGMA table_info(tool_effects)").fetchall()}
        if "args_json" not in te_cols:
            con.execute(
                "ALTER TABLE tool_effects ADD COLUMN args_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "subtask_id" not in te_cols:
            con.execute(
                "ALTER TABLE tool_effects ADD COLUMN subtask_id TEXT NOT NULL DEFAULT ''"
            )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_effects_subtask ON tool_effects(subtask_id)"
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


def resume(task_id: str, *, revalidate_effects: bool = True) -> TaskState:
    """Return the last valid state after any crash.

    When ``revalidate_effects`` is True, completed subtasks with recorded tool
    effects are checked against the current world. Drift marks the subtask
    ``needs_revalidation`` so it is not skipped.
    """
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

    if revalidate_effects:
        for s in list(subtasks):
            if s.status != "completed":
                continue
            effects = list_tool_effects_for_subtask(s.id)
            if not effects:
                continue
            result = revalidate(s.id)
            if not result.get("ok"):
                _log.info(
                    "resume revalidation failed for subtask %s: %s",
                    s.id,
                    result.get("mismatches"),
                )
        # Reload after possible status changes
        with _connect() as con:
            ensure_schema(con)
            srows = con.execute(
                "SELECT * FROM subtasks WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        subtasks = [_row_to_subtask(r) for r in srows]

    # Treat needs_revalidation as not completed
    completed = [s.id for s in subtasks if s.status == "completed"]
    # Promote needs_revalidation → ready so next_runnable can pick them up
    for s in subtasks:
        if s.status == "needs_revalidation":
            try:
                mark_subtask_status(s.id, "ready")
            except IllegalTransition:
                try:
                    mark_subtask_status(s.id, "pending")
                except IllegalTransition:
                    pass
    # Reload once more if we promoted
    with _connect() as con:
        ensure_schema(con)
        srows = con.execute(
            "SELECT * FROM subtasks WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
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
    *,
    subtask_id: str = "",
) -> None:
    h = args_hash(args)
    ts = _now()
    # Derive subtask_id from idempotency key when possible: "{task}:{sub}:{tool}"
    if not subtask_id and idempotency_key.count(":") >= 2:
        parts = idempotency_key.split(":")
        if len(parts) >= 2:
            subtask_id = parts[1]
    with _connect() as con:
        ensure_schema(con)
        con.execute(
            "INSERT INTO tool_effects "
            "(idempotency_key, tool_name, args_hash, result_json, args_json, subtask_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(idempotency_key, tool_name, args_hash) DO NOTHING",
            (
                idempotency_key,
                tool_name,
                h,
                json.dumps(result, ensure_ascii=False),
                json.dumps(args, ensure_ascii=False, default=str),
                subtask_id or "",
                ts,
            ),
        )
        con.commit()


def list_tool_effects_for_subtask(subtask_id: str) -> list[dict[str, Any]]:
    """Return recorded side-effects for a subtask (for semantic revalidation)."""
    with _connect() as con:
        ensure_schema(con)
        rows = con.execute(
            "SELECT idempotency_key, tool_name, args_hash, result_json, args_json, subtask_id, created_at "
            "FROM tool_effects WHERE subtask_id = ? ORDER BY created_at ASC",
            (subtask_id,),
        ).fetchall()
        # Fallback: match by idempotency key pattern containing subtask id
        if not rows:
            rows = con.execute(
                "SELECT idempotency_key, tool_name, args_hash, result_json, args_json, subtask_id, created_at "
                "FROM tool_effects WHERE idempotency_key LIKE ? ORDER BY created_at ASC",
                (f"%:{subtask_id}:%",),
            ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            args = json.loads(r["args_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            args = {}
        try:
            result = json.loads(r["result_json"])
        except (TypeError, json.JSONDecodeError):
            result = r["result_json"]
        out.append(
            {
                "idempotency_key": r["idempotency_key"],
                "tool_name": r["tool_name"],
                "args_hash": r["args_hash"],
                "args": args,
                "result": result,
                "subtask_id": r["subtask_id"],
                "created_at": r["created_at"],
            }
        )
    return out


def _effect_matches_world(effect: dict[str, Any], workspace: Any = None) -> tuple[bool, str]:
    """Re-check a recorded side-effect against current filesystem/world state."""
    from pathlib import Path

    from app import config as cfg

    tool = effect.get("tool_name") or ""
    args = effect.get("args") or {}
    ws = Path(workspace) if workspace else Path(getattr(cfg, "WORKSPACE_DIR", cfg.BASE_DIR))

    def _resolve(path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else ws / p

    if tool in ("write_file", "append_file", "edit_file"):
        path = str(args.get("path") or "")
        if not path:
            return True, "no path in effect args — skip check"
        p = _resolve(path)
        if not p.exists():
            return False, f"{tool}: expected file missing: {p}"
        content = str(args.get("content") or "")
        if tool == "write_file" and content:
            try:
                actual = p.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return False, f"{tool}: cannot read {p}: {exc}"
            if actual != content:
                return False, f"{tool}: content drift at {p}"
        return True, f"{tool}: {p} still present"
    if tool == "make_dir":
        path = str(args.get("path") or "")
        if not path:
            return True, "no path"
        p = _resolve(path)
        if not p.is_dir():
            return False, f"make_dir: directory missing: {p}"
        return True, f"make_dir: {p} exists"
    if tool == "remove_path":
        path = str(args.get("path") or "")
        if not path:
            return True, "no path"
        p = _resolve(path)
        if p.exists():
            return False, f"remove_path: path still exists: {p}"
        return True, f"remove_path: {p} gone"
    # Unknown / non-filesystem tools: assume still valid.
    return True, f"{tool}: no world-state check defined"


def revalidate(subtask_id: str, *, workspace: Any = None) -> dict[str, Any]:
    """Semantic checkpoint: re-check recorded tool effects against the world.

    If any effect no longer matches, mark the subtask ``needs_revalidation``
    and return diagnostic details. Caller should route through replan.
    """
    effects = list_tool_effects_for_subtask(subtask_id)
    mismatches: list[dict[str, Any]] = []
    for eff in effects:
        ok, evidence = _effect_matches_world(eff, workspace=workspace)
        if not ok:
            mismatches.append(
                {
                    "tool_name": eff.get("tool_name"),
                    "idempotency_key": eff.get("idempotency_key"),
                    "evidence": evidence,
                }
            )
    if not mismatches:
        return {"ok": True, "subtask_id": subtask_id, "mismatches": []}

    # Mark needs_revalidation so resume will not skip this work.
    try:
        sub = mark_subtask_status(subtask_id, "needs_revalidation")
        task_id = sub.task_id
    except IllegalTransition:
        # Already in a state that cannot go to needs_revalidation — force via SQL.
        with _connect() as con:
            ensure_schema(con)
            row = con.execute("SELECT * FROM subtasks WHERE id = ?", (subtask_id,)).fetchone()
            if row is None:
                raise KeyError(f"subtask not found: {subtask_id}") from None
            ts = _now()
            con.execute(
                "UPDATE subtasks SET status = ?, updated_at = ? WHERE id = ?",
                ("needs_revalidation", ts, subtask_id),
            )
            con.execute(
                "INSERT INTO events (id, task_id, subtask_id, kind, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _uid(),
                    row["task_id"],
                    subtask_id,
                    "needs_revalidation",
                    json.dumps({"mismatches": mismatches}, ensure_ascii=False),
                    ts,
                ),
            )
            con.commit()
            task_id = row["task_id"]
    else:
        record_event(
            task_id,
            subtask_id,
            "needs_revalidation",
            {"mismatches": mismatches},
        )
    return {
        "ok": False,
        "subtask_id": subtask_id,
        "task_id": task_id,
        "mismatches": mismatches,
    }


def try_complete_subtask(
    subtask_id: str,
    *,
    workspace: Any = None,
    llm_callable: Any = None,
    verify_retries: int | None = None,
) -> tuple[bool, str]:
    """Verify acceptance criteria then mark completed (or bounce to in_progress/failed).

    Returns (accepted, evidence_summary). The model's self-declared "done" is ignored
    when criteria fail.
    """
    from app import config as cfg
    from app.core.verification import evaluate_criteria

    with _connect() as con:
        ensure_schema(con)
        row = con.execute("SELECT * FROM subtasks WHERE id = ?", (subtask_id,)).fetchone()
    if row is None:
        raise KeyError(f"subtask not found: {subtask_id}")
    sub = _row_to_subtask(row)
    acceptance = sub.acceptance or {}
    # Empty acceptance → pass (tool-level subtasks often have none)
    if not acceptance or (
        isinstance(acceptance, dict) and not acceptance.get("criteria") and not acceptance.get("type")
    ):
        if isinstance(acceptance, list) and acceptance:
            pass
        else:
            mark_subtask_status(subtask_id, "completed")
            return True, "no acceptance criteria"

    all_ok, results = evaluate_criteria(
        acceptance, workspace=workspace, llm_callable=llm_callable
    )
    evidence = "; ".join(r.evidence for r in results) if results else "no criteria"
    if all_ok:
        mark_subtask_status(subtask_id, "completed")
        record_event(sub.task_id, subtask_id, "verified", {"passed": True, "evidence": evidence})
        return True, evidence

    # Fail verification — do not mark completed.
    max_retries = (
        verify_retries
        if verify_retries is not None
        else int(getattr(cfg, "VERIFIER_MAX_RETRIES", 2))
    )
    # Count prior verification failures from events
    with _connect() as con:
        ensure_schema(con)
        n_fail = con.execute(
            "SELECT COUNT(*) AS n FROM events WHERE subtask_id = ? AND kind = ?",
            (subtask_id, "verify_failed"),
        ).fetchone()["n"]
    record_event(
        sub.task_id,
        subtask_id,
        "verify_failed",
        {"passed": False, "evidence": evidence, "attempt": n_fail + 1},
    )
    if n_fail + 1 > max_retries:
        try:
            mark_subtask_status(subtask_id, "failed")
        except IllegalTransition:
            pass
        return False, f"failed after retries: {evidence}"
    # Bounce back to in_progress so the doer can fix
    try:
        if sub.status != "in_progress":
            # from completed attempt path we might be in_progress already
            pass
        # Stay / return to in_progress
        if sub.status == "completed":
            pass  # should not happen — we never marked completed
        mark_subtask_status(subtask_id, "in_progress") if sub.status in (
            "ready",
            "pending",
        ) else None
    except IllegalTransition:
        pass
    return False, evidence


def try_complete_task(
    task_id: str,
    *,
    workspace: Any = None,
    llm_callable: Any = None,
    summary_path: str | None = None,
) -> tuple[bool, str]:
    """Completion gate: all subtasks completed + task-level acceptance + summary artifact.

    On failure the task is set to ``failed`` (kernel status) with a structured diagnostic
    event. (Runner maps this to blocked/failed as appropriate.)
    """
    from pathlib import Path

    from app.core.verification import evaluate_criteria

    state = resume(task_id)
    incomplete = [s for s in state.subtasks if s.status not in ("completed", "cancelled")]
    if incomplete:
        diag = {
            "reason": "incomplete_subtasks",
            "subtask_ids": [s.id for s in incomplete],
            "statuses": {s.id: s.status for s in incomplete},
        }
        record_event(task_id, None, "task_blocked", diag)
        set_task_status(task_id, "failed")
        return False, json.dumps(diag, ensure_ascii=False)

    all_ok, results = evaluate_criteria(
        state.task.acceptance, workspace=workspace, llm_callable=llm_callable
    )
    evidence_list = [{"type": r.criterion_type, "passed": r.passed, "evidence": r.evidence} for r in results]
    if not all_ok:
        diag = {"reason": "acceptance_failed", "results": evidence_list}
        record_event(task_id, None, "task_blocked", diag)
        set_task_status(task_id, "failed")
        return False, json.dumps(diag, ensure_ascii=False)

    # Write summary artifact with links to evidence
    summary = {
        "task_id": task_id,
        "objective": state.task.objective,
        "status": "completed",
        "subtasks": [s.to_dict() for s in state.subtasks],
        "acceptance_results": evidence_list,
    }
    if summary_path:
        sp = Path(summary_path)
    else:
        from app import config as cfg

        sp = Path(cfg.DATA_DIR) / "summaries" / f"{task_id}.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    record_artifact(task_id, str(sp), sha256=_sha256_file(sp))
    record_event(task_id, None, "task_verified", {"summary": str(sp), "results": evidence_list})
    set_task_status(task_id, "done")
    return True, str(sp)


def _sha256_file(path: Any) -> str:
    import hashlib
    from pathlib import Path

    h = hashlib.sha256()
    try:
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


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
