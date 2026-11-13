from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from . import config, llm, store
from .runner import runner

INSTRUCTIONS = (
    "Local coding-agent dispatcher backed by llama.cpp. Create tasks with assign_task; "
    f"they run on a local gguf model ({config.DEFAULT_MODEL}) with file/shell tools. "
    "Use wait_for_task to block until a task finishes, then read its result and steps. "
    "Attach files or images by passing absolute paths in `attachments`; image tasks "
    f"automatically use the vision model ({config.DEFAULT_VISION_MODEL}). "
    f"Tasks are visible in the web board at http://{config.HOST}:{config.PORT}/."
)


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        "local-agents",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        streamable_http_path="/mcp",
        log_level="WARNING",
    )

    @mcp.tool()
    async def assign_task(
        prompt: str,
        title: str = "",
        agent: str = "",
        model: str = "",
        attachments: list[str] | None = None,
        auto_run: bool = True,
    ) -> dict:
        """Assign a task to the local agent and optionally start it immediately.

        Args:
            prompt: Full instruction for the agent.
            title: Short label; derived from the prompt when empty.
            agent: Agent name to attribute the task to (default local-agent).
            model: Model alias from list_models (default configured model).
            attachments: Absolute paths to files or images to send with the task.
            auto_run: Queue the task right away (false = save as draft).

        Returns the created task object, including its id and status.
        """
        if not prompt.strip():
            raise ValueError("prompt is required")
        task = store.create_task(
            title=title,
            prompt=prompt,
            agent=agent or config.DEFAULT_AGENT,
            model=model or config.DEFAULT_MODEL,
            source="mcp",
            auto_run=auto_run,
            attachments=attachments or [],
        )
        if auto_run:
            task = await runner.submit(task["id"])
        return task

    @mcp.tool()
    def list_tasks(status: str = "", limit: int = 20) -> list[dict]:
        """List tasks, newest first. Optional status filter: draft, queued, running, done, failed, cancelled."""
        return store.list_tasks(status=status or None, limit=limit)

    @mcp.tool()
    def get_task(task_id: str) -> dict:
        """Fetch one task by id, including its result, steps, and error."""
        task = store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        return task

    @mcp.tool()
    async def run_task(task_id: str) -> dict:
        """Queue a draft or failed task for execution."""
        task = store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if task["status"] == "running":
            return task
        return await runner.submit(task_id)

    @mcp.tool()
    async def cancel_task(task_id: str) -> dict:
        """Cancel a queued or running task."""
        task = await runner.cancel(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        return task

    @mcp.tool()
    async def wait_for_task(task_id: str, timeout_seconds: float = 300.0) -> dict:
        """Block until a task leaves queued/running, then return it. Raises on timeout."""
        deadline = asyncio.get_running_loop().time() + max(1.0, timeout_seconds)
        while True:
            task = store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if task["status"] not in ("queued", "running"):
                return task
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"task {task_id} still {task['status']} after {timeout_seconds}s")
            await asyncio.sleep(0.5)

    @mcp.tool()
    async def list_models() -> list[dict]:
        """List gguf models available under the configured models directory."""
        return await llm.list_models(running=True)

    @mcp.tool()
    async def server_status() -> dict:
        """Report board counts, active tasks, and llama.cpp runtime health."""
        return {
            "board_url": f"http://{config.HOST}:{config.PORT}/",
            "default_model": config.DEFAULT_MODEL,
            "counts": store.count_by_status(),
            "active": runner.active(),
            "llama": await llm.health(),
        }

    return mcp
