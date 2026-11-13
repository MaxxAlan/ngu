from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from . import agent, attachments, config, router, store
from .tools import memory
from .tools.common import allow_path, allow_write_path

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

            paths = [Path(p) for p in json.loads(task.get("attachments") or "[]")]
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

            user_content = attachments.build_content(task["prompt"], paths)
            creative = config.is_creative(task["prompt"])
            # Pre-authorize any external paths mentioned in creative tasks
            if creative:
                target_path, _ = config.creative_target_info(task["prompt"])
                if target_path is not None:
                    allow_write_path(target_path.parent)
                # Also allow any absolute path referenced in the prompt
                import re as _re
                for m in _re.finditer(r"[A-Za-z]:[\\/][^\s\"'<>|]*|(?<![\\w:.\\-/])/(?:[^\s\"'<>|/]+/)*[^\s\"'<>|/]+", task["prompt"]):
                    try:
                        allow_write_path(m.group(0).strip().rstrip("\\/"))
                    except Exception:  # noqa: BLE001
                        pass
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
                progress = memory.memory_search(f"task:{task_id}:progress", limit=1)
                if "no memory entries" not in progress:
                    messages.append({"role": "user", "content": f"Last known progress:\n{progress}"})
            else:
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ]

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
