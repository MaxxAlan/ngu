from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import agent, attachments, config, llm, novel, router, store
from .llama_runtime import runtime
from .models import discover_models, get_model, is_vision

# ---- terminal ----------------------------------------------------------
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
MAGENTA = "\x1b[35m"


def _enable_ansi() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - older streams
            pass
    if os.name != "nt":
        return
    try:
        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
    except Exception:  # noqa: BLE001 - not fatal
        pass


def info(msg: str) -> None:
    print(f"{DIM}{msg}{RESET}")


def err(msg: str) -> None:
    print(f"{RED}{msg}{RESET}", file=sys.stderr)


# Launcher script for the global command (Windows). Uses an absolute path so it
# works from any working directory, and fails loudly when the venv is missing
# instead of silently falling back to a random system python.
SHIM_TEMPLATE = (
    "@echo off\r\n"
    "setlocal\r\n"
    "chcp 65001 >nul\r\n"
    'set "PYTHONUTF8=1"\r\n'
    'set "LA_PROJECT=%~dp0"\r\n'
    'if "%LA_PROJECT:~-1%"=="\\" set "LA_PROJECT=%LA_PROJECT:~0,-1%"\r\n'
    'if not defined LA_WORKDIR set "LA_WORKDIR=%CD%"\r\n'
    'set "PYTHONPYCACHEPREFIX=%LA_PROJECT%\\cache\\python"\r\n'
    'if not exist "%PYTHONPYCACHEPREFIX%" mkdir "%PYTHONPYCACHEPREFIX%" >nul 2>&1\r\n'
    'cd /d "%LA_PROJECT%"\r\n'
    "\r\n"
    'set "PYTHON=%LA_PROJECT%\\.venv\\Scripts\\python.exe"\r\n'
    'if not exist "%PYTHON%" (\r\n'
    "  echo [ERROR] Chua cai moi truong Python.\r\n"
    "  echo         Chay setup.bat trong %LA_PROJECT% de cai lai.\r\n"
    "  exit /b 1\r\n"
    ")\r\n"
    "\r\n"
    '"%PYTHON%" -m app.cli %*\r\n'
)


def _is_admin() -> bool:
    """True when the current process runs elevated (Windows)."""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def compact(obj: object, limit: int = 160) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(obj)
    return text if len(text) <= limit else text[:limit] + "..."


def preview(text: str, lines: int = 6, width: int = 140) -> str:
    all_lines = text.splitlines()
    out: list[str] = []
    for line in all_lines[:lines]:
        out.append(line if len(line) <= width else line[:width] + "...")
    if len(all_lines) > lines:
        out.append(f"... ({len(all_lines) - lines} more lines)")
    return "\n".join(out)


# ---- input parsing -----------------------------------------------------
TOKEN_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'|(\S+)')


def split_input(text: str) -> tuple[str, list[str]]:
    """Separate existing file paths from prompt text. Supports drag-and-drop paths."""
    files: list[str] = []
    words: list[str] = []
    for m in TOKEN_RE.finditer(text):
        token = next(g for g in m.groups() if g is not None)
        cand = token[1:] if token.startswith("@") else token
        p = Path(cand).expanduser()
        if p.exists() and p.is_file():
            files.append(str(p.resolve()))
        else:
            words.append(token)
    return " ".join(words).strip(), files


def classify_args(tokens: list[str]) -> tuple[str, list[str]]:
    """Split argv into prompt words and real file paths (drag-and-drop args)."""
    files: list[str] = []
    words: list[str] = []
    for token in tokens:
        raw = token.strip().strip('"').strip("'")
        p = Path(raw).expanduser()
        if p.exists() and p.is_file():
            files.append(str(p.resolve()))
        else:
            words.append(token)
    return " ".join(words).strip(), files


# ---- event printer -----------------------------------------------------
class Printer:
    def __init__(self, stream: bool = True) -> None:
        self.stream = stream
        self._token_step: int | None = None
        self.text = ""

    async def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "token" and self.stream:
            delta = event.get("delta") or ""
            self.text += delta
            sys.stdout.write(delta)
            sys.stdout.flush()
            self._token_step = event.get("step")
            return
        if kind == "agent_step":
            if self.stream and self._token_step == event.get("step"):
                sys.stdout.write("\n")
                sys.stdout.flush()
            elif event.get("content"):
                print(event["content"].strip())
                print()
            return
        if kind == "tool_result":
            name = event.get("name") or "?"
            args = event.get("arguments") or {}
            print(f"{MAGENTA}[tool] {name}{RESET} {DIM}{compact(args)}{RESET}")
            if event.get("error"):
                print(f"{RED}{preview(event['error'], 4)}{RESET}")
            else:
                print(f"{DIM}{preview(event.get('result') or '', 6)}{RESET}")
            print()


# ---- shared helpers ----------------------------------------------------
def pick_model(name: str | None) -> str:
    if name:
        if name.lower() == "auto":
            return "auto"
        if get_model(name) is None:
            available = ", ".join(m.name for m in discover_models()) or "(none)"
            raise SystemExit(f"unknown model '{name}'. Available: {available}")
        return name
    default = get_model(config.DEFAULT_MODEL)
    if default is not None:
        return default.name
    models = discover_models()
    if not models:
        raise SystemExit(
            f"no .gguf models in {config.MODELS_DIR}. Put model files there or set LA_MODELS_DIR."
        )
    return models[0].name


def prepare_files(files: list[str]) -> list[Path]:
    paths = []
    for f in files:
        p = Path(f).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"file not found: {f}")
        paths.append(p)
    return paths


def maybe_vision_model(model: str, paths: list[Path]) -> str:
    has_image = any(attachments.classify(p) == "image" for p in paths)
    if has_image and not is_vision(model):
        if get_model(config.DEFAULT_VISION_MODEL):
            info(f"[image] switching to vision model {config.DEFAULT_VISION_MODEL}")
            return config.DEFAULT_VISION_MODEL
        info(f"[image] no vision model '{config.DEFAULT_VISION_MODEL}'; sending to {model}")
    return model


def trim_messages(messages: list[dict], budget_chars: int = 24000) -> list[dict]:
    """Keep the system prompt and the most recent turns within a rough budget."""
    if not messages:
        return messages
    head = messages[:1]
    tail: list[dict] = []
    used = 0
    for msg in reversed(messages[1:]):
        content = msg.get("content")
        size = len(content) if isinstance(content, str) else len(json.dumps(content, ensure_ascii=False))
        size += 200
        if tail and used + size > budget_chars:
            break
        tail.append(msg)
        used += size
    tail.reverse()
    return head + tail


def models_table() -> None:
    models = discover_models()
    if not models:
        err(f"no .gguf models in {config.MODELS_DIR}")
        return
    running = set(runtime.running())
    print(f"{BOLD}{'name':<24} {'kind':<7} {'params':<7} {'quant':<9} {'size':>8}{RESET}")
    for m in models:
        size = f"{m.size / (1024 ** 3):.2f} GB"
        mark = "*" if m.name in running else " "
        kind = "vision" if m.kind == "vision" else "text"
        print(f"{mark}{m.name:<23} {kind:<7} {m.parameter_size:<7} {m.quantization:<9} {size:>8}")
    print(f"{DIM}* = llama-server already running | dir {config.MODELS_DIR}{RESET}")


# ---- interactive model picker -------------------------------------------
def _read_key() -> str:
    """Read one key press -> 'up' | 'down' | 'enter' | 'esc' | 'ctrl-c' | char."""
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # extended key: arrows, home/end...
            code = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(code, "")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            return "ctrl-c"
        if ch == "\x1b":
            return "esc"
        return ch

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            nxt = sys.stdin.read(1)
            if nxt == "[":
                return {"A": "up", "B": "down"}.get(sys.stdin.read(1), "")
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            return "ctrl-c"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def model_picker(current: str) -> str | None:
    """Arrow-key model selector. Returns the chosen alias, None when cancelled."""
    models = discover_models()
    if not models:
        err(f"no .gguf models in {config.MODELS_DIR}")
        return None
    running = set(runtime.running())
    names = ["auto"] + [m.name for m in models]
    sel = names.index(current) if current in names else 0
    rows = len(names) + 1

    sys.stdout.write("\x1b[?25l")  # hide cursor
    try:
        while True:
            sys.stdout.write(f"\x1b[{rows}A\x1b[0J")  # clear previous frame
            out = [
                f"{BOLD}Chon model{RESET} "
                f"{DIM}(up/down de di chuyen, Enter chon, Esc huy, 1-9 chon nhanh){RESET}"
            ]
            for i, name in enumerate(names):
                mark = ">" if i == sel else " "
                if name == "auto":
                    label = f"{mark} {'auto':<26}"
                    if i == sel:
                        label = f"{GREEN}{label}{RESET}"
                    row = f"{label}{CYAN}router  Tự điều phối (Gà việc nhẹ/tay sai -> Ngon việc nặng){RESET}"
                    if current == "auto":
                        row += f" {GREEN}(current){RESET}"
                    out.append(row)
                    continue

                m = next(item for item in models if item.name == name)
                kind = "vision" if m.kind == "vision" else "text"
                star = "*" if m.name in running else " "
                size = f"{m.size / (1024 ** 3):.2f} GB"
                label = f"{mark} {m.name:<26}"
                if i == sel:
                    label = f"{GREEN}{label}{RESET}"
                row = (
                    f"{label}{DIM}{star} {kind:<7}{m.parameter_size:<6}"
                    f"{m.quantization:<10}{size:>8}{RESET}"
                )
                if m.name == current:
                    row += f" {GREEN}(current){RESET}"
                out.append(row)
            sys.stdout.write("\n".join(out) + "\n")
            sys.stdout.flush()

            key = _read_key()
            if key == "up":
                sel = (sel - 1) % len(names)
            elif key == "down":
                sel = (sel + 1) % len(names)
            elif key == "j":
                sel = (sel + 1) % len(names)
            elif key == "k":
                sel = (sel - 1) % len(names)
            elif key == "enter":
                return names[sel]
            elif key in ("esc", "ctrl-c"):
                return None
            elif key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(names):
                    return names[idx]
    finally:
        sys.stdout.write("\x1b[?25h")  # restore cursor
        sys.stdout.flush()


async def status_text() -> None:
    health = await llm.health()
    device = health.get("device")
    print(f"{BOLD}llama.cpp{RESET}")
    print(f"  server   {health['server']}")
    print(f"  present  {'yes' if health['ok'] else 'NO - set LA_LLAMA_DIR'}")
    print(f"  device   {device or '(cpu / auto)'}")
    print(f"  ctx      {health['ctx']} | ngl {health['ngl']}")
    print(f"  models   {len(health['models'])}: {', '.join(health['models']) or '-'}")
    print(f"  running  {', '.join(health['running']) or '-'}")
    try:
        counts = store.count_by_status()
    except Exception:  # noqa: BLE001 - db may not exist yet
        counts = {}
    auto = "ON" if config.AGENT_ALLOW_ABS else "OFF"
    print(f"  workspace {config.WORKSPACE_DIR}")
    print(f"  auto      {auto}  |  ask-permission: {'ON' if config.ASK_PERMISSION else 'OFF'}")
    print(f"{BOLD}tasks{RESET}  " + (", ".join(f"{k}={v}" for k, v in counts.items()) or "none"))


# ---- novel project commands ---------------------------------------------
def _novel_project(path_hint: str = "") -> novel.Project | None:
    """Find the story project: from a path in the command, else the workspace."""
    if path_hint:
        m = re.search(r"[A-Za-z]:\\[^\s\"']+", path_hint)
        if m:
            root = novel.find_project(Path(m.group(0)))
            if root is not None:
                return novel.Project(root)
    root = novel.find_project(config.WORKSPACE_DIR)
    if root is None:
        err("no story project here (need chapter*.txt). cd into the story folder first.")
        return None
    return novel.Project(root)


def _print_review(report: dict) -> None:
    if not report:
        err("no review data")
        return
    scores = report.get("scores") or {}
    print(f"{BOLD}review chapter {report.get('number')}{RESET}  overall {report.get('overall')}")
    if scores:
        print("  " + "   ".join(f"{k} {v}" for k, v in scores.items()))
    for issue in (report.get("issues") or [])[:8]:
        print(f"  {RED}issue{RESET} {issue}")
    for warn in (report.get("warnings") or [])[:5]:
        print(f"  {YELLOW}warn{RESET} {warn}")
    for sug in (report.get("suggestions") or [])[:5]:
        print(f"  {DIM}suggest{RESET} {sug}")


async def _cmd_write(text: str, model: str, stream: bool = True) -> None:
    project = _novel_project(text)
    if project is None:
        return
    rest = (text or "").strip()
    mode = "write"
    if rest in ("--plan", "plan"):
        mode, rest = "plan", ""
    elif rest in ("--review", "review"):
        mode, rest = "review", ""
    # strip a leading path (the project location) from the instruction
    rest = re.sub(r"^\s*[A-Za-z]:\\[^\s\"']+\s*", "", rest).strip()
    number = project.next_number()
    info(f"project {project.root} | chapter {number} | target {project.target_words(number)} words")
    if mode == "plan":
        plan = await novel.plan_chapter(project, number, rest, model)
        print(f"{BOLD}plan{RESET}")
        print(novel.plan_block(plan))
        return
    if mode == "review":
        last = project.latest_number()
        report = await novel.review_chapter(project, last, model)
        _print_review(report)
        project.set_chapter_meta(last, {"quality": report})
        project.save()
        return
    printer = Printer(stream=stream)
    res = await novel.run_write(project, rest, model, on_event=printer, stream=stream)
    print()
    plan = res.get("plan") or {}
    if plan:
        title = plan.get("title", "")
        pov = plan.get("pov", "")
        print(f"{BOLD}chapter {res['number']}{RESET} {DIM}{title}{RESET} {DIM}pov: {pov}{RESET}")
    _print_review(res.get("report") or {})
    info(f"saved -> {res['file']} | {res['words']} words | attempts {res['attempts']}")


async def _cmd_review(text: str, model: str) -> None:
    project = _novel_project()
    if project is None:
        return
    number = project.latest_number()
    if (text or "").strip().isdigit():
        number = int(text.strip())
    report = await novel.review_chapter(project, number, model)
    _print_review(report)
    project.set_chapter_meta(number, {"quality": report})
    project.save()


async def _cmd_lore(model: str) -> None:
    project = _novel_project()
    if project is None:
        return
    chars = await novel.extract_lore(project, model)
    if not chars:
        err("could not extract characters")
        return
    for name, info in chars.items():
        role = info.get("role", "") if isinstance(info, dict) else ""
        print(f"  {BOLD}{name}{RESET} {DIM}{role}{RESET}")
    info(f"{len(chars)} character(s) in {novel.META_NAME}")


async def _cmd_novel(text: str, model: str) -> None:
    project = _novel_project()
    if project is None:
        return
    if (text or "").strip() == "combine":
        out = novel.build_manuscript(project)
        info(f"manuscript -> {out}")
        return
    st = novel.status(project)
    print(f"{BOLD}{st['title']}{RESET} {DIM}{st['root']}{RESET}")
    print(f"  chapters {st['chapters']} | words {st['words']} | avg {st['avg_words']}/chapter")
    print(f"  next chapter {st['next_chapter']} | target {st['target_words']} words")
    q = st["quality"]
    if q.get("count"):
        print(f"  quality avg {q['avg']} | last {q['last']} | trend {q['direction']} ({q['delta']:+})")
    else:
        print("  quality: no reviews yet")
    if st["characters"]:
        print("  characters: " + ", ".join(list(project.meta.get("characters", {}))[:15]))


# ---- web UI (lazy start) -----------------------------------------------
async def _start_web_server(host: str, port: int) -> None:
    """Background task: run uvicorn web server."""
    import uvicorn

    from .api import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(), host=host, port=port, log_level="warning"))
    await server.serve()


async def _cmd_web(
    line: str,
    existing_task: asyncio.Task | None,
    loop: asyncio.AbstractEventLoop,
    args: argparse.Namespace,
) -> asyncio.Task | None:
    """Handle /web [--port N] command: lazily start the web UI server."""
    import webbrowser

    host = config.HOST
    port = config.PORT

    # Parse optional --port override
    parts = line.split()
    for idx, part in enumerate(parts):
        if part == "--port" and idx + 1 < len(parts):
            try:
                port = int(parts[idx + 1])
            except ValueError:
                err("invalid port number")
                return existing_task
        elif part.startswith("--port="):
            try:
                port = int(part.split("=", 1)[1])
            except ValueError:
                err("invalid port number")
                return existing_task

    if existing_task is not None and not existing_task.done():
        url = f"http://{host}:{port}/"
        info(f"web UI already running at {url}")
        webbrowser.open(url)
        return existing_task

    store.init_db()
    info(f"starting web UI at http://{host}:{port}/  (MCP: /mcp)")
    task = loop.create_task(_start_web_server(host, port))
    # Give it a moment to bind
    await asyncio.sleep(1.0)
    if task.done():
        exc = task.exception()
        err(f"web server failed to start: {exc}")
        return None
    url = f"http://{host}:{port}/"
    info(f"web UI running: {url}")
    webbrowser.open(url)
    return task


# ---- memory CLI helper -------------------------------------------------
def _cmd_memory_cli(line: str) -> None:
    """Handle /memory [query] – search or list persistent memory from CLI."""
    from .tools import memory as mem_tools

    rest = line[len("/memory"):].strip()
    try:
        if rest:
            result = mem_tools.memory_search(rest, limit=10)
        else:
            result = mem_tools.memory_list(limit=30)
        print(result)
    except Exception as exc:  # noqa: BLE001
        err(f"memory error: {exc}")


# ---- run ---------------------------------------------------------------
async def cmd_run(args: argparse.Namespace) -> int:
    store.init_db()
    prompt, files = classify_args(args.task)
    paths = prepare_files(files)
    if not prompt:
        prompt = "Describe the attached file(s) and explain what matters." if paths else ""
    if not prompt:
        err("empty prompt")
        return 2
    chosen_model = pick_model(args.model)
    if chosen_model == "auto":
        routed = router.route_model(prompt, has_images=bool(paths))
        model = maybe_vision_model(routed, paths)
        info(f"[auto] Điều phối -> {model}")
    else:
        model = maybe_vision_model(chosen_model, paths)
    agent_name = args.agent or config.DEFAULT_AGENT

    task = store.create_task(
        title=args.title or "",
        prompt=prompt,
        agent=agent_name,
        model=model,
        source="cli",
        auto_run=False,
        attachments=[str(p) for p in paths],
    )
    store.update_task(task["id"], status="running", started_at=store.now())
    info(f"task {task['id']} | model {model}" + (f" | {len(paths)} file(s)" if paths else ""))

    system = config.system_prompt_for(prompt)
    if config.is_creative(prompt):
        hint = config.creative_file_hint(prompt)
        if hint:
            system = f"{system}\n{hint}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": attachments.build_content(prompt, paths)},
    ]
    printer = Printer(stream=not args.no_stream)
    started = time.time()
    steps: list[dict] = []
    target, min_chars = config.creative_target_info(prompt)
    try:
        result = await agent.run_agent(
            model,
            messages,
            on_event=printer,
            max_steps=args.max_steps or config.AGENT_MAX_STEPS,
            stream=not args.no_stream,
            creative=config.is_creative(prompt),
            continue_path=str(target) if target else None,
            continue_min_chars=min_chars,
        )
        steps = result.get("steps", [])
        answer = result.get("answer") or ""
        saved = config.finalize_creative_output(prompt, answer)
        if saved:
            info(f"saved -> {saved}")
        if answer.strip() and answer.strip() not in printer.text:
            print()
            print(answer)
        if result.get("truncated"):
            answer = answer or "(step limit reached without a final answer)"
            store.update_task(
                task["id"],
                status="failed",
                result=answer,
                steps=steps,
                error=f"step limit ({args.max_steps or config.AGENT_MAX_STEPS}) reached",
                finished_at=store.now(),
            )
            print()
            info(f"stopped after {len(steps)} steps | {time.time() - started:.1f}s")
            return 1
        store.update_task(
            task["id"], status="done", result=answer, steps=steps, finished_at=store.now()
        )
        print()
        info(f"done | {len(steps)} step(s) | {time.time() - started:.1f}s | task {task['id']}")
        return 0
    except asyncio.CancelledError:
        store.update_task(
            task["id"],
            status="cancelled",
            result="",
            steps=steps,
            error="cancelled by user",
            finished_at=store.now(),
        )
        err("\ncancelled")
        raise
    except Exception as exc:  # noqa: BLE001 - surface failure in task + terminal
        store.update_task(
            task["id"],
            status="failed",
            result="",
            steps=steps,
            error=str(exc)[:2000],
            finished_at=store.now(),
        )
        err(f"\nfailed: {exc}")
        return 1


# ---- chat --------------------------------------------------------------
CHAT_HELP = f"""{BOLD}commands{RESET}
  /model [name]     mo bang chon model (up/down + Enter) hoac doi model
  /models           list local models
  /auto [on|off]    toggle auto-approve/sandbox-off (allow tools outside workspace)
  /web [--port N]   bat web UI trong browser (lazy start - chi chay khi can)
  /write <prompt>   pipeline viet chuong moi (plan -> write -> review -> retry)
  /write --plan     chi lap ke hoach chuong moi
  /write --review   chi review chuong vua viet
  /write --quick    che do viet nhanh (turn sau)
  /review [n]       review chuong n (mac dinh: chuong moi nhat)
  /lore             cap nhat danh sach nhan vat tu cac chuong
  /novel            tien do truyen (chuong, tu, quality trend)
  /novel combine    gop toan bo chuong thanh manuscript
  /attach <path>    queue a file or image for the next message
  /files            list queued attachments
  /clear-files      drop queued attachments
  /reset            clear conversation history
  /status           llama.cpp + memory status
  /memory [query]   search persistent memory (or list all when no query)
  /help             this help
  /exit, /quit      leave

{BOLD}input{RESET}
  Type a task. Drag a file onto the window or paste its path to attach it.
  Prefix a message with /auto to grant full tool access for that message.
  Ctrl+C interrupts the current run; /exit leaves.
"""


def cmd_chat(args: argparse.Namespace) -> int:
    store.init_db()
    prompt, files = classify_args(getattr(args, "files", None) or [])
    pending: list[Path] = prepare_files(files)
    if prompt:
        err(f"unexpected prompt text before chat start: {prompt!r}")

    model = pick_model(args.model)
    messages: list[dict] = [{"role": "system", "content": config.AGENT_SYSTEM_PROMPT}]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print(f"{BOLD}{CYAN}ngu{RESET} {DIM}| model {model} | llama.cpp{RESET}")
    info(f"workspace: {config.WORKSPACE_DIR}")
    if pending:
        info(f"attached: {', '.join(p.name for p in pending)}")
    if config.AGENT_ALLOW_ABS:
        info("auto mode: ON (tools can access paths outside workspace)")
    else:
        info("auto mode: OFF (ngoài workspace sẽ hỏi quyền nếu LA_ASK_PERMISSION=1, hoặc dùng /auto)")
    info("type /help for commands, /exit to leave")
    print()

    writing = False
    _web_server: asyncio.Task | None = None
    try:
        while True:
            _auto_restore = None
            try:
                line = input(f"{GREEN}you >{RESET} ").strip()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                continue

            if not line:
                continue
            if line in ("/exit", "/quit"):
                break
            if line == "/help":
                print(CHAT_HELP)
                continue
            if line == "/reset":
                messages = [{"role": "system", "content": config.AGENT_SYSTEM_PROMPT}]
                writing = False
                pending.clear()
                info("conversation reset")
                continue
            if line == "/models":
                models_table()
                continue
            if line == "/status":
                loop.run_until_complete(status_text())
                continue
            # /web [--port N] – lazy-start web UI
            if line == "/web" or line.startswith("/web "):
                _web_server = loop.run_until_complete(
                    _cmd_web(line, _web_server, loop, args)
                )
                continue
            # /memory [query] – search or list persistent memory
            if line == "/memory" or line.startswith("/memory "):
                _cmd_memory_cli(line)
                continue
            if line.startswith("/auto"):
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    arg = parts[1].strip().lower()
                    if arg in ("on", "1", "true", "yes"):
                        config.AGENT_ALLOW_ABS = True
                        info("auto mode: ON (tools can access paths outside workspace)")
                        continue
                    if arg in ("off", "0", "false", "no"):
                        config.AGENT_ALLOW_ABS = False
                        info("auto mode: OFF (tools sandboxed to workspace)")
                        continue
                    # "/auto <message>" -> run this message with auto mode temporarily
                    prev_auto = config.AGENT_ALLOW_ABS
                    config.AGENT_ALLOW_ABS = True
                    line = parts[1].strip()
                    if not line:
                        config.AGENT_ALLOW_ABS = not prev_auto
                        state = "ON" if config.AGENT_ALLOW_ABS else "OFF"
                        info(f"auto mode: {state}")
                        continue
                    # fall through to handle `line` as a normal prompt with auto on
                    _auto_restore = prev_auto
                else:
                    config.AGENT_ALLOW_ABS = not config.AGENT_ALLOW_ABS
                    state = "ON" if config.AGENT_ALLOW_ABS else "OFF"
                    extra = " (tools can access paths outside workspace)" if config.AGENT_ALLOW_ABS else " (tools sandboxed to workspace)"
                    info(f"auto mode: {state}{extra}")
                    continue
            else:
                _auto_restore = None
            if line.startswith("/model"):
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    try:
                        model = pick_model(parts[1].strip())
                    except SystemExit as exc:
                        err(str(exc))
                        continue
                    info(f"model -> {model}")
                else:
                    chosen = model_picker(model)
                    if chosen:
                        model = chosen
                        info(f"model -> {model}")
                    else:
                        info("cancelled")
                continue
            if line.startswith("/attach"):
                _, _, rest = line.partition(" ")
                _, found = split_input(rest.strip())
                if not found:
                    err("usage: /attach <path>")
                    continue
                pending.extend(prepare_files(found))
                info(f"attached: {', '.join(p.name for p in pending)}")
                continue
            if line == "/files":
                if pending:
                    for p in pending:
                        info(f"{p}  [{attachments.classify(p)}]")
                else:
                    info("no attachments queued")
                continue
            if line == "/clear-files":
                pending.clear()
                info("attachments cleared")
                continue
            if line == "/write" or line.startswith("/write "):
                rest = line[len("/write"):].strip()
                if rest in ("--quick", "quick"):
                    writing = True
                    info("quick writing mode ON (next message is written as prose)")
                    continue
                loop.run_until_complete(_cmd_write(rest, model, stream=not args.no_stream))
                continue
            if line == "/review" or line.startswith("/review "):
                rest = line[len("/review"):].strip()
                loop.run_until_complete(_cmd_review(rest, model))
                continue
            if line == "/lore":
                loop.run_until_complete(_cmd_lore(model))
                continue
            if line == "/novel" or line.startswith("/novel "):
                rest = line[len("/novel"):].strip()
                loop.run_until_complete(_cmd_novel(rest, model))
                continue
            if line.startswith("/"):
                err(f"unknown command: {line.split()[0]} (try /help)")
                continue

            text, found = split_input(line)
            paths = pending + prepare_files(found)
            pending = []
            if not text and paths:
                text = "Describe the attached file(s) and explain what matters."
            if not text:
                continue

            if model == "auto":
                routed = router.route_model(text, has_images=bool(paths))
                turn_model = maybe_vision_model(routed, paths)
                info(f"[auto] Điều phối -> {turn_model}")
            else:
                turn_model = maybe_vision_model(model, paths)
            user_content = attachments.build_content(text, paths)
            messages.append({"role": "user", "content": user_content})
            messages[:] = trim_messages(messages)
            creative = writing or config.is_creative(text)
            system = config.AGENT_SYSTEM_PROMPT
            if creative:
                system = config.CREATIVE_SYSTEM_PROMPT
                hint = config.creative_file_hint(text)
                if hint:
                    system = f"{system}\n{hint}"
            messages[0] = {"role": "system", "content": system}

            print(f"{CYAN}agent >{RESET}")
            printer = Printer(stream=not args.no_stream)
            started = time.time()
            target, min_chars = config.creative_target_info(text)
            try:
                result = loop.run_until_complete(
                    agent.run_agent(
                        turn_model,
                        messages,
                        on_event=printer,
                        max_steps=args.max_steps or config.AGENT_MAX_STEPS,
                        stream=not args.no_stream,
                        creative=creative,
                        continue_path=str(target) if target else None,
                        continue_min_chars=min_chars,
                    )
                )
                if result.get("truncated") and not result.get("answer"):
                    err(f"step limit ({args.max_steps or config.AGENT_MAX_STEPS}) reached")
                final = result.get("answer") or ""
                saved = config.finalize_creative_output(text, final)
                if saved:
                    info(f"saved -> {saved}")
                if final.strip() and final.strip() not in printer.text:
                    print(final)
                print()
                info(f"{len(result.get('steps', []))} step(s) | {time.time() - started:.1f}s")
            except KeyboardInterrupt:
                print()
                err("interrupted")
                while messages and messages[-1]["role"] != "user":
                    messages.pop()
            except Exception as exc:  # noqa: BLE001 - keep the REPL alive
                print()
                err(f"error: {exc}")
                while messages and messages[-1]["role"] != "user":
                    messages.pop()
            finally:
                if _auto_restore is not None:
                    config.AGENT_ALLOW_ABS = _auto_restore
                    _auto_restore = None
            writing = False
            print()
    finally:
        if _web_server is not None and not _web_server.done():
            _web_server.cancel()
        loop.run_until_complete(runtime.stop_all())
        loop.close()
    return 0


# ---- other commands ----------------------------------------------------
async def cmd_models(args: argparse.Namespace) -> int:
    models_table()
    return 0


async def cmd_tasks(args: argparse.Namespace) -> int:
    store.init_db()
    tasks = store.list_tasks(status=args.status or None, limit=args.limit)
    if not tasks:
        info("no tasks")
        return 0
    for t in tasks:
        title = (t.get("title") or "").replace("\n", " ")[:60]
        print(f"{t['id']}  {t['status']:<9} {t['model']:<20} {title}")
    return 0


async def cmd_show(args: argparse.Namespace) -> int:
    store.init_db()
    task = store.get_task(args.id)
    if task is None:
        err(f"unknown task: {args.id}")
        return 1
    print(f"{BOLD}{task['id']}{RESET}  {task['status']}  {task['model']}")
    info(f"agent {task['agent']} | created {task['created_at']} | finished {task.get('finished_at')}")
    print(f"\n{BOLD}prompt{RESET}\n{task['prompt']}")
    atts = json.loads(task.get("attachments") or "[]")
    if atts:
        print(f"\n{BOLD}attachments{RESET}")
        for a in atts:
            print(f"  {a}")
    steps = json.loads(task.get("steps") or "[]")
    if steps:
        print(f"\n{BOLD}steps ({len(steps)}){RESET}")
        for s in steps:
            calls = s.get("tool_calls") or []
            names = ", ".join(str((c or {}).get("name")) for c in calls) or "-"
            print(f"  [{s.get('index')}] tools: {names}")
    if task.get("result"):
        print(f"\n{BOLD}result{RESET}\n{task['result']}")
    if task.get("error"):
        print(f"\n{BOLD}error{RESET}\n{task['error']}")
    return 0


async def cmd_cancel(args: argparse.Namespace) -> int:
    store.init_db()
    task = store.get_task(args.id)
    if task is None:
        err(f"unknown task: {args.id}")
        return 1
    if task["status"] in ("queued", "running"):
        store.update_task(args.id, status="cancelled", error="cancelled", finished_at=store.now())
        info(f"task {args.id}: {task['status']} -> cancelled")
    else:
        info(f"task {args.id} is {task['status']}; nothing to cancel")
    return 0


async def cmd_rm(args: argparse.Namespace) -> int:
    store.init_db()
    if not store.delete_task(args.id):
        err(f"unknown task: {args.id}")
        return 1
    info(f"deleted {args.id}")
    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    await status_text()
    return 0


async def cmd_install(args: argparse.Namespace) -> int:
    """Create a global launcher command and add project dir to PATH."""
    name = (getattr(args, "name", None) or config.COMMAND_NAME or "ngu").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        err(f"invalid command name: {name!r} (use letters/numbers/._-)")
        return 2
    root = config.BASE_DIR
    shim_path = root / f"{name}.cmd"
    shim_path.write_text(SHIM_TEMPLATE, encoding="utf-8", newline="")
    info(f"created launcher: {shim_path}")

    scope = (getattr(args, "scope", None) or "auto").strip().lower()
    if scope not in ("auto", "user", "machine"):
        scope = "auto"
    if scope == "auto":
        scope = "machine" if _is_admin() else "user"

    # Add project dir to PATH (avoid duplicating; avoid setx PATH truncation)
    try:
        ps = (
            "$dir = $env:LA_PROJECT_DIR; "
            "$scope = $env:LA_PATH_SCOPE; "
            "$cur = [Environment]::GetEnvironmentVariable('Path', $scope); "
            "if (-not $cur) { $cur = '' }; "
            "$parts = $cur -split ';' | Where-Object { $_ -ne '' }; "
            "if ($parts -notcontains $dir) { "
            "  $new = ($parts + $dir) -join ';'; "
            "  [Environment]::SetEnvironmentVariable('Path',$new,$scope); "
            "  Write-Output 'PATH_ADDED' "
            "} else { Write-Output 'PATH_EXISTS' }"
        )
        env = os.environ.copy()
        env["LA_PROJECT_DIR"] = str(root)
        env["LA_PATH_SCOPE"] = scope
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        out = (res.stdout or "").strip()
        if "PATH_ADDED" in out:
            info(f"added to {scope} PATH: {root}")
            info("open a new terminal, then run: " + name)
        elif "PATH_EXISTS" in out:
            info(f"{scope} PATH already contains: {root}")
        else:
            err(f"could not update {scope} PATH automatically: {(res.stderr or res.stdout or '').strip()}")
            info(f"add manually to PATH: {root}")
    except Exception as exc:  # noqa: BLE001
        err(f"could not update PATH: {exc}")
        info(f"add manually to PATH: {root}")

    print()
    info(f"usage: {name}              # chat trong thư mục hiện tại")
    info(f"       {name} run \"task\"   # chạy 1 task")
    info(f"       {name} status")
    return 0


async def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    host = args.host or config.HOST
    port = args.port or config.PORT
    info(f"serving http://{host}:{port}/  (mcp: /mcp)")
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host=host, port=port, log_level="info")
    )
    try:
        await server.serve()
    finally:
        await runtime.stop_all()
    return 0


async def cmd_novel(args: argparse.Namespace) -> int:
    """Novel project status / manuscript build."""
    root = novel.find_project(config.WORKSPACE_DIR)
    if root is None:
        err(f"no story project in {config.WORKSPACE_DIR} (need chapter*.txt)")
        return 1
    project = novel.Project(root)
    action = (getattr(args, "action", "") or "status").strip().lower()
    if action == "combine":
        out = novel.build_manuscript(project)
        info(f"manuscript -> {out}")
        return 0
    st = novel.status(project)
    print(f"{BOLD}{st['title']}{RESET} {DIM}{st['root']}{RESET}")
    print(f"  chapters {st['chapters']} | words {st['words']} | avg {st['avg_words']}/chapter")
    print(f"  next chapter {st['next_chapter']} | target {st['target_words']} words")
    q = st["quality"]
    if q.get("count"):
        print(f"  quality avg {q['avg']} | last {q['last']} | trend {q['direction']} ({q['delta']:+})")
    else:
        print("  quality: no reviews yet")
    if st["characters"]:
        print("  characters: " + ", ".join(list(project.meta.get("characters", {}))[:15]))
    return 0


# ---- entry point -------------------------------------------------------
COMMANDS = {"run", "chat", "models", "tasks", "show", "cancel", "rm", "status", "serve", "install", "novel"}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--model", "-m", default=None, help=f"model alias (default: {config.DEFAULT_MODEL})"
    )
    common.add_argument("--no-stream", action="store_true", help="disable token streaming")
    common.add_argument("--max-steps", type=int, default=None, help="tool loop step limit")

    p = argparse.ArgumentParser(
        prog="ngu",
        description="ngu: local AI agent chạy trên llama.cpp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            '  ngu run "viet fizzbuzz.py va test"\n'
            '  ngu run "anh nay la gi?" photo.png\n'
            "  ngu\n"
            "  ngu chat notes.md diagram.png\n"
            "  ngu models\n"
            "  ngu serve\n"
        ),
        parents=[common],
    )
    sub = p.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="run one task to completion", parents=[common])
    run.add_argument("task", nargs="+", help="prompt, plus optional file/image paths")
    run.add_argument("--agent", "-a", default=None)
    run.add_argument("--title", default="")

    chat = sub.add_parser("chat", help="interactive REPL (default)", parents=[common])
    chat.add_argument("files", nargs="*", help="files/images to attach to the first message")

    sub.add_parser("models", help="list local .gguf models")

    tasks = sub.add_parser("tasks", help="list tasks")
    tasks.add_argument("--status", "-s", default="")
    tasks.add_argument("--limit", "-n", type=int, default=30)

    show = sub.add_parser("show", help="show one task")
    show.add_argument("id")

    cancel = sub.add_parser("cancel", help="cancel a task")
    cancel.add_argument("id")

    rm = sub.add_parser("rm", help="delete a task")
    rm.add_argument("id")

    sub.add_parser("status", help="llama.cpp + task status")

    serve = sub.add_parser("serve", help="run the web board + MCP server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    install = sub.add_parser("install", help="tạo lệnh global (LA_COMMAND_NAME) và thêm vào PATH")
    install.add_argument("--name", default=None, help="tên lệnh (mặc định lấy từ .config LA_COMMAND_NAME)")
    install.add_argument(
        "--scope",
        default="auto",
        choices=["auto", "user", "machine"],
        help="phạm vi PATH: auto (machine nếu chạy admin, ngược lại user)",
    )

    novel_cmd = sub.add_parser("novel", help="novel project: status | combine")
    novel_cmd.add_argument("action", nargs="?", default="status", help="status (default) | combine")
    return p


def main(argv: list[str] | None = None) -> int:
    _enable_ansi()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    argv = list(sys.argv[1:] if argv is None else argv)
    # `ngu "do something"` -> implicit run; dropped files alone -> chat with attachments
    if argv and not argv[0].startswith("-") and argv[0] not in COMMANDS:
        all_files = all(
            Path(a.strip('"')).exists() and Path(a.strip('"')).is_file() for a in argv
        )
        argv = (["chat"] if all_files else ["run"]) + argv

    args = build_parser().parse_args(argv)
    cmd = args.cmd or "chat"
    if not hasattr(args, "files"):
        args.files = []
    if not hasattr(args, "task"):
        args.task = []
    if not hasattr(args, "status"):
        args.status = ""
    if not hasattr(args, "limit"):
        args.limit = 30

    if cmd == "chat":
        return cmd_chat(args)

    handler = {
        "run": cmd_run,
        "models": cmd_models,
        "tasks": cmd_tasks,
        "show": cmd_show,
        "cancel": cmd_cancel,
        "rm": cmd_rm,
        "status": cmd_status,
        "serve": cmd_serve,
        "install": cmd_install,
        "novel": cmd_novel,
    }[cmd]

    async def runner_main() -> int:
        try:
            return await handler(args)
        finally:
            await runtime.stop_all()

    try:
        return asyncio.run(runner_main())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
