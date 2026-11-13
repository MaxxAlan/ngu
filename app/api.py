from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import attachments, config, llm, store
from .llama_runtime import runtime
from .mcp_server import build_mcp
from .models import discover_models, get_model
from .router import get_available_roles
from .runner import runner, running

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class TaskIn(BaseModel):
    prompt: str = Field(min_length=1)
    title: str = ""
    agent: str | None = None
    model: str | None = None
    auto_run: bool = True
    attachments: list[str] = Field(default_factory=list)


class ChatIn(BaseModel):
    messages: list[dict]
    model: str | None = None
    stream: bool = True


def create_app() -> FastAPI:
    mcp = build_mcp()
    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        store.init_db()
        async with mcp.session_manager.run():
            async with running():
                yield
        await runtime.stop_all()

    app = FastAPI(title="local-agents", lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "board_url": f"http://{config.HOST}:{config.PORT}/board",
            "default_model": config.DEFAULT_MODEL,
            "counts": store.count_by_status(),
            "active": runner.active(),
            "llama": await llm.health(),
        }

    @app.get("/api/models")
    async def models() -> list[dict]:
        return await llm.list_models(running=True)

    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...)) -> dict:
        session = uuid.uuid4().hex[:12]
        saved = []
        for f in files:
            data = await f.read()
            path = attachments.save_upload(session, f.filename or "file", data)
            saved.append(attachments.describe(path))
        return {"session": session, "files": saved}

    @app.get("/api/tasks")
    async def tasks(status: str | None = None, limit: int = 50) -> list[dict]:
        return store.list_tasks(status=status or None, limit=limit)

    @app.post("/api/tasks", status_code=201)
    async def create_task(body: TaskIn) -> dict:
        task = store.create_task(
            title=body.title,
            prompt=body.prompt,
            agent=body.agent,
            model=body.model,
            source="ui",
            auto_run=body.auto_run,
            attachments=body.attachments,
        )
        if body.auto_run:
            task = await runner.submit(task["id"])
        return task

    @app.get("/api/tasks/{task_id}")
    async def read_task(task_id: str) -> dict:
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        return task

    @app.post("/api/tasks/{task_id}/run")
    async def run_task(task_id: str) -> dict:
        if store.get_task(task_id) is None:
            raise HTTPException(404, "task not found")
        return await runner.submit(task_id)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str) -> dict:
        task = await runner.cancel(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        return task

    @app.delete("/api/tasks/{task_id}", status_code=204)
    async def delete_task(task_id: str) -> None:
        if not store.delete_task(task_id):
            raise HTTPException(404, "task not found")

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        queue = runner.subscribe()

        async def gen():
            try:
                yield ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                runner.unsubscribe(queue)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        """Main UI = llama.cpp's own chat page, not the task-board."""
        model = get_model(config.DEFAULT_MODEL) or next(iter(discover_models()), None)
        if model is None:
            return RedirectResponse("/board")
        base = await runtime.ensure(model)
        return RedirectResponse(base + "/")

    @app.get("/board", include_in_schema=False)
    async def board() -> FileResponse:
        return FileResponse(config.STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
    # Streamable-HTTP MCP endpoint lives at /mcp (see mcp_server.build_mcp).
    app.mount("/", mcp_app, name="mcp")

    return app
