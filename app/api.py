from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import attachments, config, llm, store
from .llama_runtime import runtime
from .mcp_server import build_mcp
from .models import discover_models, get_model
from .runner import runner, running
from .tools.common import PermissionDenied, resolve_attachment_path
from .tools.memory import memory_prune

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("local-agents.api")

_bearer = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _token_from_request(
    creds: HTTPAuthorizationCredentials | None,
    api_key: str | None,
) -> str | None:
    if creds and creds.scheme.lower() == "bearer" and creds.credentials:
        return creds.credentials.strip()
    if api_key and api_key.strip():
        return api_key.strip()
    return None


def require_api_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    api_key: str | None = Depends(_api_key_header),
) -> None:
    """Enforce LA_API_TOKEN when configured. Public only when token is empty (local dev)."""
    expected = config.API_TOKEN
    if not expected:
        return
    provided = _token_from_request(creds, api_key)
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="unauthorized: missing or invalid API token")


def _warn_if_exposed_without_token() -> None:
    if config.API_TOKEN:
        return
    if config.is_loopback_host():
        return
    log.warning(
        "LA_HOST=%s binds beyond loopback but LA_API_TOKEN is empty — "
        "HTTP API, /board, and MCP are unauthenticated. Set LA_API_TOKEN before exposing.",
        config.HOST,
    )




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

    async def _health_watchdog() -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await runtime.healthcheck_all()
            except Exception:  # noqa: BLE001 - watchdog must never die
                log.exception("health watchdog iteration failed")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        store.init_db()
        memory_prune(config.MEMORY_MAX_ENTRIES, config.MEMORY_MAX_AGE_DAYS)
        watchdog = asyncio.create_task(_health_watchdog())
        async with mcp.session_manager.run():
            async with running():
                yield
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
        await runtime.stop_all()

    app = FastAPI(title="local-agents", lifespan=lifespan)

    @app.middleware("http")
    async def api_token_middleware(request: Request, call_next):
        """Require LA_API_TOKEN for non-public routes when the token is configured.

        Public without token: /api/health (k8s probes). Everything else under
        /api/*, /board, /mcp, and the MCP mount requires a matching
        Authorization: Bearer <token> or X-API-Key header when LA_API_TOKEN is set.
        """
        path = request.url.path
        if not config.API_TOKEN:
            return await call_next(request)
        if path == "/api/health" or path.startswith("/api/health/"):
            return await call_next(request)
        # static assets for the board still need auth when token is set (same origin fetch
        # should send the header; unauthenticated browsers get 401).
        auth = request.headers.get("authorization") or ""
        api_key = request.headers.get("x-api-key") or ""
        provided = None
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        elif api_key.strip():
            provided = api_key.strip()
        if provided != config.API_TOKEN:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "unauthorized: missing or invalid API token"}, status_code=401)
        return await call_next(request)


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
        safe_attachments: list[str] = []
        for raw in body.attachments or []:
            try:
                safe_attachments.append(str(resolve_attachment_path(raw)))
            except PermissionDenied as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        task = store.create_task(
            title=body.title,
            prompt=body.prompt,
            agent=body.agent,
            model=body.model,
            source="ui",
            auto_run=body.auto_run,
            attachments=safe_attachments,
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
