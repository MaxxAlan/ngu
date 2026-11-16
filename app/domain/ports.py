"""Outbound ports (interfaces) that infrastructure implements and application uses."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .task import TaskRecord


@runtime_checkable
class TaskRepositoryPort(Protocol):
    def init_db(self) -> None: ...

    def create_task(
        self,
        title: str,
        prompt: str,
        agent: str | None = None,
        model: str | None = None,
        source: str = "ui",
        auto_run: bool = True,
        attachments: list[str] | None = None,
    ) -> TaskRecord: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def list_tasks(self, status: str | None = None, limit: int = 50) -> list[TaskRecord]: ...

    def update_task(self, task_id: str, **fields: Any) -> TaskRecord | None: ...

    def delete_task(self, task_id: str) -> bool: ...

    def count_by_status(self) -> dict[str, int]: ...


@runtime_checkable
class AgentRunnerPort(Protocol):
    async def submit(self, task_id: str) -> TaskRecord: ...

    async def cancel(self, task_id: str) -> TaskRecord | None: ...

    def active(self) -> list[str]: ...


@runtime_checkable
class LLMGatewayPort(Protocol):
    async def list_models(self, running: bool = False) -> list[dict]: ...

    async def health(self) -> dict: ...
