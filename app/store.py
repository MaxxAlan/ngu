from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from . import config

STATUSES = ("draft", "queued", "running", "done", "failed", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    agent       TEXT NOT NULL,
    model       TEXT NOT NULL,
    status      TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'ui',
    result      TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    steps       TEXT NOT NULL DEFAULT '[]',
    messages        TEXT NOT NULL DEFAULT '[]',
    resume_attempts INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, created_at);
"""

# columns added after the original schema; migrated in init_db
MIGRATIONS = {
    "attachments": "ALTER TABLE tasks ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'",
    "steps": "ALTER TABLE tasks ADD COLUMN steps TEXT NOT NULL DEFAULT '[]'",
    "messages": "ALTER TABLE tasks ADD COLUMN messages TEXT NOT NULL DEFAULT '[]'",
    "resume_attempts": "ALTER TABLE tasks ADD COLUMN resume_attempts INTEGER NOT NULL DEFAULT 0",
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def init_db() -> None:
    with _connect() as con:
        con.executescript(SCHEMA)
        existing = {r["name"] for r in con.execute("PRAGMA table_info(tasks)").fetchall()}
        for col, ddl in MIGRATIONS.items():
            if col not in existing:
                con.execute(ddl)
        # Tasks interrupted by a restart: resume from their checkpointed messages
        # if any exist and the retry budget isn't exhausted, else mark failed.
        resumable = con.execute(
            "SELECT id FROM tasks WHERE status='running' AND messages != '[]' "
            "AND resume_attempts < ?",
            (config.MAX_RESUME_ATTEMPTS,),
        ).fetchall()
        if resumable:
            ids = [r["id"] for r in resumable]
            con.executemany(
                "UPDATE tasks SET status='queued', resume_attempts=resume_attempts+1 WHERE id=?",
                [(i,) for i in ids],
            )
        con.execute(
            "UPDATE tasks SET status='failed', error='interrupted by restart', finished_at=? "
            "WHERE status='running'",
            (now(),),
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def create_task(
    title: str,
    prompt: str,
    agent: str | None = None,
    model: str | None = None,
    source: str = "ui",
    auto_run: bool = True,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    task_id = uuid.uuid4().hex[:12]
    row = {
        "id": task_id,
        "title": title.strip() or prompt.strip().splitlines()[0][:80],
        "prompt": prompt.strip(),
        "agent": (agent or config.DEFAULT_AGENT).strip(),
        "model": (model or config.DEFAULT_MODEL).strip(),
        "status": "queued" if auto_run else "draft",
        "source": source,
        "result": "",
        "error": "",
        "attachments": json.dumps(attachments or []),
        "steps": "[]",
        "created_at": now(),
        "started_at": None,
        "finished_at": None,
    }
    with _connect() as con:
        con.execute(
            "INSERT INTO tasks (id, title, prompt, agent, model, status, source, result, error,"
            " attachments, steps, created_at, started_at, finished_at)"
            " VALUES (:id, :title, :prompt, :agent, :model, :status, :source, :result, :error,"
            " :attachments, :steps, :created_at, :started_at, :finished_at)",
            row,
        )
    return row


def get_task(task_id: str) -> dict[str, Any] | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_tasks(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    with _connect() as con:
        if status:
            rows = con.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_task(task_id: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {
        "title",
        "prompt",
        "agent",
        "model",
        "status",
        "result",
        "error",
        "attachments",
        "steps",
        "messages",
        "resume_attempts",
        "started_at",
        "finished_at",
    }
    patch = {k: v for k, v in fields.items() if k in allowed}
    for k in ("attachments", "steps", "messages"):
        if k in patch and not isinstance(patch[k], str):
            patch[k] = json.dumps(patch[k])
    if not patch:
        return get_task(task_id)
    assignments = ", ".join(f"{k} = :{k}" for k in patch)
    patch["id"] = task_id
    with _connect() as con:
        con.execute(f"UPDATE tasks SET {assignments} WHERE id = :id", patch)
    return get_task(task_id)


def delete_task(task_id: str) -> bool:
    with _connect() as con:
        cur = con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return cur.rowcount > 0


def count_by_status() -> dict[str, int]:
    with _connect() as con:
        rows = con.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
