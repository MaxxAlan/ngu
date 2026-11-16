"""Reproducible Phase 0 benchmark harness.

Modes:
  - reference: execute each task's reference_plan via tools (deterministic baseline)
  - live: run app.agent.run_agent against a real model (optional)

Every run records: task_id, mode, model, ctx, steps, tool_calls, elapsed_ms,
rss_mb, outcome (pass/fail), check_results, answer preview.
"""
from __future__ import annotations

import hashlib
import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"
BASELINE_DIR = ROOT / "benchmarks" / "baseline"


@dataclass
class CheckResult:
    type: str
    ok: bool
    detail: str = ""


@dataclass
class TaskResult:
    task_id: str
    category: str
    mode: str
    model: str
    ctx: int
    steps: int
    tool_calls: int
    tool_names: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    rss_mb: float | None = None
    outcome: str = "fail"  # pass | fail | error
    answer_preview: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class SuiteResult:
    suite: str
    version: int
    catalog_sha256: str
    mode: str
    model: str
    ctx: int
    started_at: str
    finished_at: str
    elapsed_ms: float
    passed: int
    failed: int
    errors: int
    tasks: list[TaskResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "version": self.version,
            "catalog_sha256": self.catalog_sha256,
            "mode": self.mode,
            "model": self.model,
            "ctx": self.ctx,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_ms": self.elapsed_ms,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "pass_rate": round(self.passed / max(1, self.passed + self.failed + self.errors), 4),
            "tasks": [t.to_dict() for t in self.tasks],
        }


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    p = path or CATALOG_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data.get("tasks"), "catalog has no tasks"
    return data


def catalog_sha256(path: Path | None = None) -> str:
    raw = (path or CATALOG_PATH).read_bytes()
    return hashlib.sha256(raw).hexdigest()


def _rss_mb() -> float | None:
    try:
        import resource

        # Linux: ru_maxrss is kB; macOS: bytes
        val = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if val > 10_000_000:  # likely bytes (mac)
            return round(val / (1024 * 1024), 2)
        return round(val / 1024, 2)
    except Exception:  # noqa: BLE001
        return None


def setup_workspace(task: dict[str, Any], ws: Path) -> None:
    files = (task.get("setup") or {}).get("files") or {}
    for rel, content in files.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _file_text(ws: Path, rel: str) -> str | None:
    p = ws / rel
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def evaluate_checks(
    task: dict[str, Any],
    ws: Path,
    answer: str,
    steps: list[dict[str, Any]],
) -> list[CheckResult]:
    tool_names: list[str] = []
    tool_args: list[dict[str, Any]] = []
    for step in steps:
        for tc in step.get("tool_calls") or []:
            name = tc.get("name") or ""
            tool_names.append(name)
            args = tc.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_args.append({"name": name, "arguments": args})

    results: list[CheckResult] = []
    for chk in task.get("checks") or []:
        ctype = chk["type"]
        ok = False
        detail = ""
        if ctype == "file_exists":
            ok = (ws / chk["path"]).is_file()
            detail = chk["path"]
        elif ctype == "file_not_exists":
            ok = not (ws / chk["path"]).exists()
            detail = chk["path"]
        elif ctype == "dir_exists":
            ok = (ws / chk["path"]).is_dir()
            detail = chk["path"]
        elif ctype == "file_contains":
            text = _file_text(ws, chk["path"]) or ""
            ok = chk["value"] in text
            detail = f"{chk['path']} has {chk['value']!r}"
        elif ctype == "file_not_contains":
            text = _file_text(ws, chk["path"]) or ""
            ok = chk["value"] not in text
            detail = f"{chk['path']} lacks {chk['value']!r}"
        elif ctype == "file_equals":
            text = _file_text(ws, chk["path"])
            ok = text == chk["value"]
            detail = chk["path"]
        elif ctype == "answer_contains":
            ok = chk["value"] in (answer or "")
            detail = chk["value"]
        elif ctype == "tool_used":
            ok = chk["name"] in tool_names
            detail = chk["name"]
        elif ctype == "tool_not_used":
            ok = chk["name"] not in tool_names
            detail = chk["name"]
        elif ctype == "tool_not_used_on":
            name = chk["name"]
            path = chk["path"]
            touched = False
            for ta in tool_args:
                if ta["name"] != name:
                    continue
                args = ta["arguments"]
                for key in ("path", "dst", "src"):
                    if str(args.get(key, "")) == path:
                        touched = True
            ok = not touched
            detail = f"{name} on {path}"
        elif ctype == "max_steps":
            ok = len(steps) <= int(chk["value"])
            detail = f"steps={len(steps)} <= {chk['value']}"
        elif ctype == "max_tool_calls":
            ok = len(tool_names) <= int(chk["value"])
            detail = f"tool_calls={len(tool_names)} <= {chk['value']}"
        else:
            detail = f"unknown check {ctype}"
            ok = False
        results.append(CheckResult(type=ctype, ok=ok, detail=detail))
    return results


async def run_reference_task(task: dict[str, Any], ws: Path) -> tuple[str, list[dict[str, Any]]]:
    """Deterministic agent: execute reference_plan tool calls in order."""
    from app.tools.registry import execute

    steps: list[dict[str, Any]] = []
    chunks: list[str] = []
    for i, call in enumerate(task.get("reference_plan") or []):
        name = call["name"]
        args = call.get("arguments") or {}
        try:
            out = await execute(name, args)
        except Exception as exc:  # noqa: BLE001
            out = f"ERROR: {exc}"
        chunks.append(str(out))
        steps.append(
            {
                "index": i,
                "content": "",
                "tool_calls": [{"name": name, "arguments": args, "result": str(out)[:2000]}],
            }
        )
    answer = "\n".join(chunks)
    steps.append({"index": len(steps), "content": answer, "tool_calls": []})
    return answer, steps


async def run_live_task(task: dict[str, Any], model: str) -> tuple[str, list[dict[str, Any]]]:
    from app.agent import run_agent

    messages = [{"role": "user", "content": task["prompt"]}]
    result = await run_agent(model=model, messages=messages, max_steps=12)
    return result.get("answer") or "", result.get("steps") or []


async def run_one(
    task: dict[str, Any],
    *,
    mode: str,
    model: str,
    ctx: int,
    workspace: Path,
) -> TaskResult:
    setup_workspace(task, workspace)
    t0 = time.perf_counter()
    error = ""
    answer = ""
    steps: list[dict[str, Any]] = []
    try:
        if mode == "reference":
            answer, steps = await run_reference_task(task, workspace)
        elif mode == "live":
            answer, steps = await run_live_task(task, model)
        else:
            raise ValueError(f"unknown mode: {mode}")
        outcome_checks = evaluate_checks(task, workspace, answer, steps)
        ok = all(c.ok for c in outcome_checks) if outcome_checks else False
        outcome = "pass" if ok else "fail"
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[:800]}"
        outcome_checks = []
        outcome = "error"

    tool_names: list[str] = []
    for step in steps:
        for tc in step.get("tool_calls") or []:
            if tc.get("name"):
                tool_names.append(tc["name"])

    elapsed = (time.perf_counter() - t0) * 1000
    return TaskResult(
        task_id=task["id"],
        category=task.get("category", ""),
        mode=mode,
        model=model,
        ctx=ctx,
        steps=len(steps),
        tool_calls=len(tool_names),
        tool_names=tool_names,
        elapsed_ms=round(elapsed, 2),
        rss_mb=_rss_mb(),
        outcome=outcome,
        answer_preview=(answer or "")[:400],
        checks=outcome_checks,
        error=error,
    )


async def run_suite(
    *,
    mode: str = "reference",
    model: str = "reference",
    ctx: int | None = None,
    task_ids: list[str] | None = None,
    work_root: Path | None = None,
) -> SuiteResult:
    from app import config
    from app.tools import set_workspace

    catalog = load_catalog()
    ctx_val = int(ctx if ctx is not None else getattr(config, "LLAMA_CTX", 0) or 0)
    tasks = catalog["tasks"]
    if task_ids:
        want = set(task_ids)
        tasks = [t for t in tasks if t["id"] in want]

    root = work_root or (ROOT / "benchmarks" / "work")
    root.mkdir(parents=True, exist_ok=True)

    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = time.perf_counter()
    results: list[TaskResult] = []

    for task in tasks:
        ws = root / mode / task["id"]
        if ws.exists():
            # clean slate
            import shutil

            shutil.rmtree(ws)
        ws.mkdir(parents=True)
        set_workspace(ws)
        # isolate memory db per task
        data = ws / ".data"
        data.mkdir(exist_ok=True)
        config.DATA_DIR = data
        config.UPLOAD_DIR = data / "uploads"
        config.UPLOAD_DIR.mkdir(exist_ok=True)
        tr = await run_one(task, mode=mode, model=model, ctx=ctx_val, workspace=ws)
        results.append(tr)

    finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    passed = sum(1 for r in results if r.outcome == "pass")
    failed = sum(1 for r in results if r.outcome == "fail")
    errors = sum(1 for r in results if r.outcome == "error")
    return SuiteResult(
        suite=catalog.get("name", "ngu-benchmark"),
        version=int(catalog.get("version", 1)),
        catalog_sha256=catalog_sha256(),
        mode=mode,
        model=model,
        ctx=ctx_val,
        started_at=started,
        finished_at=finished,
        elapsed_ms=round((time.perf_counter() - t0) * 1000, 2),
        passed=passed,
        failed=failed,
        errors=errors,
        tasks=results,
    )


def write_results(suite: SuiteResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(suite.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def compare_to_baseline(
    current: SuiteResult,
    baseline_path: Path,
    *,
    require_same_catalog: bool = True,
) -> list[str]:
    """Return list of gate failure messages (empty = pass)."""
    if not baseline_path.is_file():
        return [f"baseline missing: {baseline_path}"]
    base = json.loads(baseline_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if require_same_catalog and base.get("catalog_sha256") != current.catalog_sha256:
        problems.append(
            f"catalog_sha256 changed: baseline={base.get('catalog_sha256')} current={current.catalog_sha256}"
        )
    if base.get("mode") != current.mode:
        problems.append(f"mode mismatch: baseline={base.get('mode')} current={current.mode}")
    base_tasks = {t["task_id"]: t for t in base.get("tasks", [])}
    cur_tasks = {t.task_id: t for t in current.tasks}
    if set(base_tasks) != set(cur_tasks):
        problems.append(
            f"task id set mismatch: missing={sorted(set(base_tasks)-set(cur_tasks))} "
            f"extra={sorted(set(cur_tasks)-set(base_tasks))}"
        )
    for tid, bt in base_tasks.items():
        ct = cur_tasks.get(tid)
        if ct is None:
            continue
        if bt.get("outcome") != ct.outcome:
            problems.append(f"{tid}: outcome {bt.get('outcome')} -> {ct.outcome}")
    if current.passed < base.get("passed", 0):
        problems.append(f"passed count regressed: {base.get('passed')} -> {current.passed}")
    return problems
