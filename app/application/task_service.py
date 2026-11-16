"""Task use cases shared by HTTP API and MCP adapters.

Validates attachment paths at the application boundary so every entry point
(API, MCP, future workers) applies the same rules without duplicating logic.
"""
from __future__ import annotations

from typing import Any

from app.domain.ports import AgentRunnerPort, TaskRepositoryPort
from app.tools.common import resolve_attachment_path


class TaskService:
    """Application service for task lifecycle operations."""

    def __init__(
        self,
        repo: TaskRepositoryPort,
        runner: AgentRunnerPort | None = None,
    ) -> None:
        self._repo = repo
        self._runner = runner

    def bind_runner(self, runner: AgentRunnerPort) -> None:
        self._runner = runner

    @staticmethod
    def normalize_attachments(paths: list[str] | None) -> list[str]:
        """Resolve and jail attachment paths; raise PermissionDenied on violation."""
        safe: list[str] = []
        for raw in paths or []:
            safe.append(str(resolve_attachment_path(raw)))
        return safe

    def create_task(
        self,
        *,
        prompt: str,
        title: str = "",
        agent: str | None = None,
        model: str | None = None,
        source: str = "ui",
        auto_run: bool = True,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        if not (prompt or "").strip():
            raise ValueError("prompt is required")
        safe = self.normalize_attachments(attachments)
        return self._repo.create_task(
            title=title,
            prompt=prompt,
            agent=agent,
            model=model,
            source=source,
            auto_run=auto_run,
            attachments=safe,
        )

    async def create_and_maybe_run(
        self,
        *,
        prompt: str,
        title: str = "",
        agent: str | None = None,
        model: str | None = None,
        source: str = "ui",
        auto_run: bool = True,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        task = self.create_task(
            prompt=prompt,
            title=title,
            agent=agent,
            model=model,
            source=source,
            auto_run=auto_run,
            attachments=attachments,
        )
        if auto_run and self._runner is not None:
            task = await self._runner.submit(task["id"])
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._repo.get_task(task_id)

    def list_tasks(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self._repo.list_tasks(status=status, limit=limit)

    def delete_task(self, task_id: str) -> bool:
        return self._repo.delete_task(task_id)

    async def run_task(self, task_id: str) -> dict[str, Any]:
        if self._runner is None:
            raise RuntimeError("runner not bound")
        return await self._runner.submit(task_id)

    async def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        if self._runner is None:
            raise RuntimeError("runner not bound")
        return await self._runner.cancel(task_id)


_default: TaskService | None = None


def get_task_service() -> TaskService:
    """Process-wide TaskService wired to the default SQLite repo and runner."""
    global _default
    if _default is None:
        from app import store
        from app.runner import runner

        _default = TaskService(repo=store, runner=runner)
    return _default
