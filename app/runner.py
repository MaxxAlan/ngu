from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from . import agent, attachments, config, router, store
from .tools import memory
from .tools.common import PermissionDenied, path_permission_scope, resolve_attachment_path

log = logging.getLogger("local-agents.runner")


class Runner:
    """Single-process task executor: queue -> agent loop -> sqlite -> SSE subscribers."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._subs: set[asyncio.Queue] = set()
        self._tasks: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(config.MAX_CONCURRENCY)
        self._stopping = False

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        self._stopping = False

    async def stop(self) -> None:
        self._stopping = True
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ---- pub/sub -------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _emit(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._subs.discard(q)

    def _emit_task(self, task: dict) -> None:
        self._emit({"type": "task", "task": task})

    # ---- public API ----------------------------------------------------
    async def submit(self, task_id: str) -> dict:
        task = store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["status"] == "running":
            return task
        task = store.update_task(task_id, status="queued", error="", finished_at=None) or task
        self._emit_task(task)
        await self._queue.put(task_id)
        return task

    async def cancel(self, task_id: str) -> dict | None:
        task = store.get_task(task_id)
        if task is None:
            return None
        running = self._tasks.get(task_id)
        if running and not running.done():
            running.cancel()
            return store.get_task(task_id)
        if task["status"] == "queued":
            return store.update_task(
                task_id, status="cancelled", error="cancelled before start", finished_at=store.now()
            )
        return task

    def active(self) -> list[str]:
        return [tid for tid, t in self._tasks.items() if not t.done()]

    # ---- worker --------------------------------------------------------
    async def _worker(self) -> None:
        while not self._stopping:
            task_id = await self._queue.get()
            try:
                task = store.get_task(task_id)
                if task is None or task["status"] != "queued":
                    continue
                job = asyncio.create_task(self._run(task_id))
                self._tasks[task_id] = job
                job.add_done_callback(lambda _t, tid=task_id: self._tasks.pop(tid, None))
            finally:
                self._queue.task_done()

    async def _run(self, task_id: str) -> None:
        async with self._sem:
            task = store.get_task(task_id)
            if task is None or task["status"] != "queued":
                return
            task = store.update_task(
                task_id, status="running", started_at=store.now(), result="", error="", steps=[]
            ) or task
            self._emit_task(task)

            # Attachments: only paths under UPLOAD_DIR or workspace (no arbitrary abs paths).
            raw_attachments = json.loads(task.get("attachments") or "[]")
            paths: list[Path] = []
            for raw in raw_attachments:
                try:
                    paths.append(resolve_attachment_path(raw))
                except PermissionDenied as exc:
                    store.update_task(
                        task_id,
                        status="failed",
                        error=str(exc),
                        finished_at=store.now(),
                    )
                    task = store.get_task(task_id) or task
                    self._emit_task(task)
                    log.warning("task %s rejected attachment %r: %s", task_id, raw, exc)
                    return

            has_image = any(attachments.classify(p) == "image" for p in paths if p.exists())
            model = task.get("model") or "auto"
            if model == "auto":
                model = router.route_model(task["prompt"], has_images=has_image)
                store.update_task(task_id, model=model)
                log.info("task %s auto-routed to %s", task_id, model)
            elif has_image and not _model_supports_vision(model):
                model = config.DEFAULT_VISION_MODEL
                store.update_task(task_id, model=model)
                log.info("task %s has images; switching to vision model %s", task_id, model)

            # Isolate path grants for this task so ALLOWED_* is not process-global.
            with path_permission_scope():
                await self._execute_task_body(task_id, task, paths, model)
            return

    async def _execute_task_body(self, task_id: str, task: dict, paths: list, model: str) -> None:
            """Agent loop body; runs inside path_permission_scope()."""
            user_content = attachments.build_content(task["prompt"], paths)
            creative = config.is_creative(task["prompt"])
            # Do NOT auto-grant write access from prompt text. Outside-workspace
            # writes go through resolve_path/_ask_permission (interactive CLI) or
            # fail closed (API/MCP) unless LA_AGENT_ALLOW_ABS=1.
            system = config.CREATIVE_SYSTEM_PROMPT if creative else config.AGENT_SYSTEM_PROMPT
            if creative:
                hint = config.creative_file_hint(task["prompt"])
                if hint:
                    # Prepend hint to user message so the model sees exact target file/length
                    user_content_text = hint + "\n\n" + (
                        user_content if isinstance(user_content, str)
                        else next((p["text"] for p in user_content if isinstance(p, dict) and p.get("type") == "text"), "")
                    )
                    if isinstance(user_content, str):
                        user_content = user_content_text
                    elif isinstance(user_content, list):
                        # Prepend to first text part
                        patched = False
                        new_content = []
                        for part in user_content:
                            if not patched and isinstance(part, dict) and part.get("type") == "text":
                                new_content.append({"type": "text", "text": hint + "\n\n" + part.get("text", "")})
                                patched = True
                            else:
                                new_content.append(part)
                        if not patched:
                            new_content.insert(0, {"type": "text", "text": hint})
                        user_content = new_content

            checkpoint = json.loads(task.get("messages") or "[]")
            resuming = bool(checkpoint)
            if resuming:
                messages = checkpoint
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Resuming after an interruption. Continue the task using the "
                            "context above; do not repeat work already done."
                        ),
                    }
                )
                log.info("task %s resuming from checkpoint (%d messages)", task_id, len(messages))
                try:
                    progress = memory.memory_get(f"task:{task_id}:progress")
                except Exception:
                    progress = ""
                if progress and "no memory entry" not in progress.lower():
                    messages.append({"role": "user", "content": f"Last known progress:\n{progress}"})
            else:
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ]

            # Phase 1: durable task kernel (optional, behind LA_TASK_KERNEL).
            kernel_task_id: str | None = None
            if getattr(config, "TASK_KERNEL", False):
                try:
                    from app.core import task_kernel as tk

                    # Prefer an existing kernel task linked by legacy id; else create.
                    existing = None
                    try:
                        # Look up by using legacy_task_id column via a direct query helper
                        from app.core.task_kernel import _connect, ensure_schema

                        with _connect() as con:
                            ensure_schema(con)
                            row = con.execute(
                                "SELECT id FROM kernel_tasks WHERE legacy_task_id = ? "
                                "ORDER BY created_at DESC LIMIT 1",
                                (task_id,),
                            ).fetchone()
                            if row:
                                existing = row["id"]
                    except Exception:  # noqa: BLE001
                        existing = None
                    if existing:
                        kernel_task_id = existing
                        state = tk.resume(kernel_task_id)
                        log.info(
                            "task %s kernel resume: completed=%d next=%s",
                            task_id,
                            len(state.completed_subtask_ids),
                            state.next_subtask.id if state.next_subtask else None,
                        )
                        if state.completed_subtask_ids:
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Durable checkpoint: the following subtask IDs are already "
                                        f"completed and must NOT be repeated: "
                                        f"{', '.join(state.completed_subtask_ids[:40])}."
                                    ),
                                }
                            )
                    else:
                        kt = tk.create_task(
                            objective=task.get("prompt") or task.get("title") or task_id,
                            constraints={"source": task.get("source", "ui")},
                            acceptance={},
                            legacy_task_id=task_id,
                            status="running",
                        )
                        kernel_task_id = kt.id
                        tk.checkpoint(kernel_task_id)
                        log.info("task %s kernel created id=%s", task_id, kernel_task_id)
                except Exception as exc:  # noqa: BLE001 - kernel is best-effort
                    log.warning("task kernel unavailable for %s: %s", task_id, exc)
                    kernel_task_id = None

            steps: list[dict] = []
            step_count = 0

            async def on_event(event: dict) -> None:
                nonlocal step_count
                if event.get("type") == "agent_step":
                    step_count += 1
                    steps.append(
                        {
                            "index": event.get("step"),
                            "content": event.get("content", ""),
                            "tool_calls": event.get("tool_calls", []),
                            "tool_results": [],
                        }
                    )
                    store.update_task(task_id, steps=steps, messages=messages)
                    if step_count % config.CHECKPOINT_INTERVAL == 0:
                        summary = (event.get("content") or "")[:500] or json.dumps(
                            event.get("tool_calls") or [], ensure_ascii=False
                        )[:500]
                        if summary.strip():
                            memory.memory_save(f"task:{task_id}:progress", summary, tags="checkpoint")
                elif event.get("type") == "tool_result":
                    for s in reversed(steps):
                        if s.get("index") == event.get("step"):
                            s.setdefault("tool_results", []).append(
                                {
                                    "name": event.get("name"),
                                    "result": event.get("result"),
                                    "error": event.get("error"),
                                }
                            )
                            break
                    store.update_task(task_id, steps=steps, messages=messages)
                event = {**event, "task_id": task_id}
                self._emit(event)

            try:
                target, min_chars = config.creative_target_info(task["prompt"])
                result = await asyncio.wait_for(
                    agent.run_agent(
                        model,
                        messages,
                        on_event=on_event,
                        stream=True,
                        creative=config.is_creative(task["prompt"]),
                        continue_path=str(target) if target else None,
                        continue_min_chars=min_chars,
                        kernel_task_id=kernel_task_id,
                    ),
                    timeout=config.TASK_TIMEOUT,
                )
                answer = result.get("answer") or ""
                saved = config.finalize_creative_output(task["prompt"], answer)
                if saved:
                    # Clean echoed path lines / ellipsis markers from the written file
                    target_path = config.creative_target(task["prompt"])
                    if target_path and target_path.exists():
                        config.clean_story_file(target_path)
                    answer = f"{answer}\n\nSaved: {saved}"
                    log.info("task %s saved creative output -> %s", task_id, saved)
                if kernel_task_id and getattr(config, "TASK_KERNEL", False):
                    try:
                        from app.core import task_kernel as tk

                        # Phase 4 completion gate: subtasks + acceptance + summary
                        ok, evidence = tk.try_complete_task(kernel_task_id)
                        if not ok:
                            log.warning(
                                "task %s kernel completion blocked: %s",
                                task_id,
                                evidence[:400],
                            )
                            final = store.update_task(
                                task_id,
                                status="failed",
                                result=answer,
                                steps=result.get("steps", steps),
                                messages="[]",
                                error=f"acceptance criteria failed: {evidence[:500]}",
                                finished_at=store.now(),
                            )
                            self._emit_task(final or task)
                            return
                        tk.checkpoint(kernel_task_id)
                    except Exception:  # noqa: BLE001
                        pass
                final = store.update_task(
                    task_id,
                    status="done",
                    result=answer,
                    steps=result.get("steps", steps),
                    messages="[]",
                    finished_at=store.now(),
                )
                self._emit_task(final or task)
            except asyncio.CancelledError:
                if kernel_task_id and getattr(config, "TASK_KERNEL", False):
                    try:
                        from app.core import task_kernel as tk

                        tk.set_task_status(kernel_task_id, "cancelled")
                    except Exception:  # noqa: BLE001
                        pass
                final = store.update_task(
                    task_id,
                    status="cancelled",
                    result="",
                    steps=steps,
                    messages="[]",
                    error="cancelled",
                    finished_at=store.now(),
                )
                self._emit_task(final or task)
                raise
            except TimeoutError:
                # Keep the checkpoint: a restart (or manual resubmit) can resume from it.
                log.warning("task %s timed out after %ss", task_id, config.TASK_TIMEOUT)
                if kernel_task_id and getattr(config, "TASK_KERNEL", False):
                    try:
                        from app.core import task_kernel as tk

                        tk.set_task_status(kernel_task_id, "failed")
                        tk.checkpoint(kernel_task_id)
                    except Exception:  # noqa: BLE001
                        pass
                final = store.update_task(
                    task_id,
                    status="failed",
                    result="",
                    steps=steps,
                    error=f"timed out after {config.TASK_TIMEOUT}s (resumable)",
                    finished_at=store.now(),
                )
                self._emit_task(final or task)
            except Exception as exc:  # noqa: BLE001 - failure is a task state
                log.exception("task %s failed", task_id)
                if kernel_task_id and getattr(config, "TASK_KERNEL", False):
                    try:
                        from app.core import task_kernel as tk

                        tk.set_task_status(kernel_task_id, "failed")
                        tk.checkpoint(kernel_task_id)
                    except Exception:  # noqa: BLE001
                        pass
                final = store.update_task(
                    task_id,
                    status="failed",
                    result="",
                    steps=steps,
                    error=str(exc)[:2000],
                    finished_at=store.now(),
                )
                self._emit_task(final or task)


def _model_supports_vision(model: str) -> bool:
    from .models import discover_models

    for m in discover_models():
        if m.name == model:
            return m.kind == "vision"
    return False


runner = Runner()


@contextlib.asynccontextmanager
async def running():
    worker = asyncio.create_task(runner._worker())
    await runner.start()
    try:
        yield runner
    finally:
        await runner.stop()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
