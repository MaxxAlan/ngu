from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import config, llm, tools
from .buffer_station import station

log = logging.getLogger("local-agents.agent")

EventCb = Callable[[dict], Awaitable[None] | None]

_STEP_MAX_TOKENS = 1536
_FINAL_MAX_TOKENS = 4096

# Max consecutive tool-call-only steps without a text answer before we force a reply.
_MAX_SILENT_TOOL_STEPS = 4


def _user_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                t = p.get("text") or p.get("content") or ""
                if isinstance(t, str):
                    parts.append(t)
        return " ".join(parts)
    return ""


def _is_creative_messages(messages: list[dict]) -> bool:
    for m in reversed(messages):
        if m.get("role") == "user":
            return config.is_creative(_user_text(m))
    return False


# Tools that only look at the workspace: if the model already produced the
# deliverable, calling them again is noise.
_EXPLORE_TOOLS = {"list_dir", "list_tree", "glob_files", "search_files", "read_file"}


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return _user_text(m)
    return ""


def _only_tool_json(content: str) -> bool:
    """True when the message is nothing but fenced tool-call blocks."""
    stripped = (content or "").strip()
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", stripped, re.S)
    if not blocks:
        return False
    if re.sub(r"```(?:json)?\s*(.*?)```", "", stripped, flags=re.S).strip():
        return False
    known = {t["function"]["name"] for t in tools.TOOLS}
    for b in blocks:
        obj = _parse_json(b)
        if not obj:
            continue
        norm = _normalize(obj)
        if norm and norm["name"] in known:
            return True
    return False


def _looks_like_answer(content: str) -> bool:
    """The message already carries the deliverable (code block or real prose)."""
    stripped = (content or "").strip()
    if not stripped or _only_tool_json(stripped):
        return False
    if "```" in stripped:
        return True
    return len(stripped) >= 280


def _effective_tool(name: str, args: dict, continue_path: str | None) -> str:
    """Never let the model overwrite a chapter that is being built up.

    write_file on an existing target becomes append_file, so a scene written
    later cannot wipe the scenes before it.
    """
    if name != "write_file" or not continue_path:
        return name
    raw = str(args.get("path", "") or "")
    if not raw:
        return name
    try:
        target = str(Path(raw).resolve()).lower()
        cont = str(Path(continue_path).resolve()).lower()
    except OSError:
        return name
    if target == cont and Path(continue_path).exists():
        return "append_file"
    return name


async def _plain_answer(
    model: str,
    messages: list[dict],
    max_tokens: int,
    stream: bool,
    emit: EventCb,
) -> str:
    """Single completion without tools (chit-chat / direct answers)."""
    if stream:
        parts: list[str] = []
        async for event in llm.stream_chat_events(model, messages, tools=None, max_tokens=max_tokens):
            piece = event.get("content")
            if piece:
                parts.append(piece)
                await emit({"type": "token", "step": 0, "delta": piece})
        return "".join(parts).strip()
    resp = await llm.chat(model, messages, tools=None, max_tokens=max_tokens)
    choice = resp.get("choices", [{}])[0]
    return ((choice.get("message") or {}).get("content") or "").strip()


def _file_chars(path: str) -> int:
    try:
        return len(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def _write_text(path: str, text: str) -> bool:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


def _continuation_msg(current: int, target: int) -> str:
    return (
        f"The chapter is not finished yet: only {current} of at least {target} characters are written. "
        "Continue the story from exactly where it stopped (never repeat text that is already in the file) "
        "and add the next part with append_file to the same path. "
        "Keep writing until the chapter is long enough."
    )


async def _final_answer(model: str, messages: list[dict], max_tokens: int) -> str:
    """Force a plain-text final answer, no tool calls allowed."""
    try:
        resp = await llm.chat(
            model,
            messages
            + [
                {
                    "role": "user",
                    "content": (
                        "No more tools. Give the final answer now in plain text, "
                        "using the information you already gathered."
                    ),
                }
            ],
            max_tokens=max_tokens,
        )
    except Exception:  # noqa: BLE001 - best effort
        return ""
    choice = resp.get("choices", [{}])[0]
    return ((choice.get("message") or {}).get("content") or "").strip()


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _normalize(obj: dict) -> dict | None:
    name = obj.get("name") or obj.get("tool") or obj.get("function")
    args = obj.get("arguments") or obj.get("args") or obj.get("parameters") or {}
    if isinstance(name, dict):
        fn = name
        name = fn.get("name")
        args = fn.get("arguments") or args
    if not isinstance(name, str) or not name:
        return None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    if not isinstance(args, dict):
        args = {"value": args}
    return {"name": name.strip(), "arguments": args}


def _tool_keys(obj: dict) -> bool:
    return bool(obj) and set(obj) <= {"name", "tool", "function", "arguments", "args", "parameters"}


def parse_tool_calls(content: str, *, strict: bool = True) -> list[dict]:
    """Extract at most 1 tool call from model text when strict (text protocol)."""
    known = {t["function"]["name"] for t in tools.TOOLS}

    def pick(obj: dict | None) -> dict | None:
        if not obj or not _tool_keys(obj):
            return None
        norm = _normalize(obj)
        if not norm:
            return None
        if strict and norm["name"] not in known:
            return None
        return norm

    # 1) explicit <tool_call>
    for m in re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", content, re.S):
        norm = pick(_parse_json(m.group(1)))
        if norm:
            return [norm]

    # 2) fenced json blocks - take first valid known tool call; ignore plans/noise
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", content, re.S):
        norm = pick(_parse_json(m.group(1)))
        if norm:
            return [norm]

    # 3) bare JSON only if whole/primary content is a single tool object
    if strict:
        stripped = content.strip()
        obj = _parse_json(stripped)
        norm = pick(obj)
        if norm and stripped.startswith("{") and stripped.endswith("}"):
            return [norm]
    return []


def _merge_tool_call_deltas(chunks: list[dict]) -> list[dict]:
    """Merge OpenAI streaming tool_call deltas into complete calls."""
    merged: dict[int, dict] = {}
    for chunk in chunks:
        idx = chunk.get("index", 0)
        slot = merged.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
        if chunk.get("id"):
            slot["id"] = chunk["id"]
        fn = chunk.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] += fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]
    out = []
    for slot in merged.values():
        if slot["function"]["name"]:
            out.append(slot)
    return out


def _call_sig(name: str, args: Any) -> str:
    try:
        payload = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:  # noqa: BLE001
        payload = str(args)
    return f"{name}:{payload[:500]}"


def _trim_messages(messages: list[dict], keep_head: int = 2, keep_tail: int = 24) -> None:
    # keep system (+ first user goal) and recent tail to bound context growth
    if len(messages) <= keep_head + keep_tail:
        return
    del messages[keep_head:-keep_tail]


async def run_agent(
    model: str,
    messages: list[dict],
    on_event: EventCb | None = None,
    max_steps: int | None = None,
    stream: bool = False,
    creative: bool | None = None,
    continue_path: str | None = None,
    continue_min_chars: int = 0,
    call_tokens: int | None = None,
) -> dict:
    """Run the tool-use loop. Returns {"answer": str, "steps": [...]}."""
    max_steps = max_steps or config.AGENT_MAX_STEPS
    creative_flag = creative if creative is not None else _is_creative_messages(messages)
    final_max_tokens = config.CREATIVE_FINAL_MAX_TOKENS if creative_flag else config.FINAL_MAX_TOKENS
    steps: list[dict] = []
    seen_calls: dict[str, int] = {}
    seen_tool_names: dict[str, int] = {}
    seen_errors: dict[str, int] = {}
    nudges = 0
    continuations = 0
    max_continuations = 6
    # Track consecutive steps with tool calls but no meaningful text reply.
    silent_tool_steps = 0

    async def emit(event: dict) -> None:
        if on_event is None:
            return
        res = on_event(event)
        if hasattr(res, "__await__"):
            await res

    # 1. Buffer Station Cache Check (Zero GPU inference)
    cached_reply = station.get_cached_response(model, messages)
    if cached_reply:
        steps.append({"index": 0, "content": cached_reply, "tool_calls": []})
        await emit({"type": "token", "step": 0, "delta": cached_reply})
        await emit({"type": "agent_step", "step": 0, "content": cached_reply, "tool_calls": []})
        return {"answer": cached_reply, "steps": steps}

    # Chit-chat / plain questions: don't advertise tools -> model answers, no tool loop.
    last_text = _last_user_text(messages)
    has_atts = any("--- file:" in str(m.get("content", "")) or "--- file attached:" in str(m.get("content", "")) for m in messages)
    use_tools = config.needs_tools(last_text, creative=creative_flag, has_attachments=has_atts)
    if not use_tools:
        content = await _plain_answer(model, messages, final_max_tokens, stream, emit)
        steps.append({"index": 0, "content": content, "tool_calls": []})
        await emit({"type": "agent_step", "step": 0, "content": content, "tool_calls": []})
        if content:
            station.store_cached_response(model, messages, content)
        return {"answer": content, "steps": steps}

    force_final = False
    step_max_tokens = call_tokens or (
        config.CREATIVE_FINAL_MAX_TOKENS if creative_flag else _STEP_MAX_TOKENS
    )
    llm_failures = 0

    for i in range(max_steps):
        # 2. VRAM Protection: Compress old turns and truncate ancient tool logs
        messages[:] = station.compress_messages_for_vram(messages, max_total_chars=max(4000, config.LLAMA_CTX * 3))
        _trim_messages(messages)
        native: list[dict] = []
        req_max = step_max_tokens

        # --- Force a plain-text answer if model has been silent too long ---
        if silent_tool_steps >= _MAX_SILENT_TOOL_STEPS:
            answer = await _final_answer(model, messages, final_max_tokens)
            if answer.strip():
                return {"answer": answer, "steps": steps}
            # reset counter and add a stern nudge
            silent_tool_steps = 0
            messages.append({
                "role": "user",
                "content": (
                    "IMPORTANT: Stop calling tools and provide the final answer in plain text now. "
                    "Summarize what you found and answer the user's question directly."
                ),
            })

        try:
            if stream:
                parts: list[str] = []
                chunks: list[dict] = []
                buf = ""
                decided = False
                suppress = False
                async for event in llm.stream_chat_events(
                    model, messages, tools=tools.to_openai_tools(), max_tokens=req_max
                ):
                    piece = event.get("content")
                    if piece:
                        parts.append(piece)
                        if not decided:
                            # hold back a little to see whether this is a tool-call payload
                            buf += piece
                            if len(buf.strip()) >= 24:
                                head = buf.lstrip()
                                suppress = (
                                    head.startswith("```json")
                                    or head.startswith("<tool_call")
                                    or head.startswith("{")
                                )
                                decided = True
                                if not suppress:
                                    await emit({"type": "token", "step": i, "delta": buf})
                        else:
                            await emit({"type": "token", "step": i, "delta": piece})
                    call = event.get("tool_call")
                    if call:
                        chunks.append(call)
                if not decided and buf:
                    head = buf.lstrip()
                    if not (
                        head.startswith("```json")
                        or head.startswith("<tool_call")
                        or head.startswith("{")
                    ):
                        await emit({"type": "token", "step": i, "delta": buf})
                content = "".join(parts)
                native = _merge_tool_call_deltas(chunks)
            else:
                resp = await llm.chat(model, messages, tools=tools.to_openai_tools(), max_tokens=req_max)
                choice = resp.get("choices", [{}])[0]
                msg = choice.get("message") or {}
                content = msg.get("content") or ""
                native = msg.get("tool_calls") or []
        except Exception as exc:  # noqa: BLE001 - a broken step must not kill the run
            llm_failures += 1
            log.warning("llm step failed (step %s): %s", i, exc)
            if llm_failures <= 3 and i < max_steps - 1:
                step_max_tokens = min(step_max_tokens, 4096)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The previous response failed - it was probably too long and got cut off. "
                            "Continue with SMALL tool calls: at most ~2500 characters of content per call, "
                            "and use append_file for every following part of a long text."
                        ),
                    }
                )
                continue
            return {"answer": "", "steps": steps, "truncated": True}

        # Prefer native tool_calls; parse text only as fallback.
        if native:
            calls = list(native)
            used_native = True
        else:
            calls = parse_tool_calls(content, strict=True)
            used_native = False

        # Normalize to single call per step for stability (esp. small models).
        if calls and not used_native:
            calls = calls[:1]

        tool_calls_view = [
            {
                "name": (c.get("function") or {}).get("name") or c.get("name"),
                "arguments": (c.get("function") or {}).get("arguments") or c.get("arguments"),
            }
            for c in calls
        ]
        step = {"index": i, "content": content, "tool_calls": tool_calls_view}
        steps.append(step)
        display = "" if _only_tool_json(content) else content
        await emit({"type": "agent_step", "step": i, "content": display, "tool_calls": tool_calls_view})

        # Model already produced the deliverable; an extra exploration call is noise.
        if calls and content and _looks_like_answer(content):
            names = {(c.get("function") or {}).get("name") or c.get("name") for c in calls}
            if names and names <= _EXPLORE_TOOLS:
                return {"answer": content, "steps": steps}

        if not calls:
            stripped = (content or "").strip()
            # Reset silent counter – model produced text
            silent_tool_steps = 0

            # Creative: keep the chapter on disk and long enough.
            if continue_path:
                if not Path(continue_path).exists() and len(stripped) >= 400:
                    _write_text(continue_path, stripped + "\n")
                cur = _file_chars(continue_path)
                if (
                    cur < continue_min_chars
                    and continuations < max_continuations
                    and (cur > 0 or len(stripped) >= 400)
                    and i < max_steps - 1
                ):
                    continuations += 1
                    messages.append(
                        {"role": "user", "content": _continuation_msg(cur, continue_min_chars)}
                    )
                    continue

            # If model emitted a plan/fake JSON but nothing executable, nudge once/twice.
            looks_like_tool = bool(re.search(r"```(?:json)?|<tool_call>|\"name\"\s*:\s*\"(read_file|write_file|edit_file|list_dir|search_files|run_shell|memory_save|memory_search)", stripped))
            if not stripped:
                if nudges < 2:
                    nudges += 1
                    messages.append({"role": "user", "content": "No output. Reply with the final answer in plain text, or exactly one tool call JSON block."})
                    continue
                return {"answer": "", "steps": steps, "truncated": True}
            if looks_like_tool and nudges < 3 and i < max_steps - 1:
                nudges += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Tool call not parsed. Send EXACTLY ONE fenced JSON block calling a real tool "
                            "(one of: read_file, write_file, edit_file, list_dir, search_files, run_shell, memory_save, memory_search), "
                            "or reply with the final answer in plain text. Do not list multiple/planned calls."
                        ),
                    }
                )
                continue
            # Final answer path (or best-effort).
            if content:
                station.store_cached_response(model, messages, content)
            return {"answer": content, "steps": steps}

        # Has tool calls – track silent steps (tool calls without useful text answer)
        has_text_answer = bool((content or "").strip()) and not _only_tool_json(content)
        if has_text_answer:
            silent_tool_steps = 0
        else:
            silent_tool_steps += 1

        # Record assistant message
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
        if used_native:
            assistant_msg["tool_calls"] = calls
        messages.append(assistant_msg)

        # Execute (single call in text mode; may be multiple in native mode)
        progress = False
        for j, call in enumerate(calls):
            fn = call.get("function") or call
            name = fn.get("name") or ""
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            if not isinstance(args, dict):
                args = {"value": args}
            broken_args = set(args) == {"_raw"}

            sig = _call_sig(name, args)
            seen_calls[sig] = seen_calls.get(sig, 0) + 1
            repeated = seen_calls[sig] >= 2
            name_hits = seen_tool_names.get(name, 0) + 1
            seen_tool_names[name] = name_hits
            call_id = call.get("id") or f"call_{i}_{j}"

            if broken_args:
                error = (
                    "tool call arguments were not valid JSON (the response was cut off). "
                    "Keep every tool call small: at most ~2500 characters of content per call, "
                    "then use append_file for the next part."
                )
                result = ""
                payload = error
                log.warning("tool %s got unparseable arguments", name)
            elif repeated or name_hits >= 4:
                error = (
                    f"repeated identical tool call blocked: {name} "
                    f"{json.dumps(args, ensure_ascii=False)[:200]}"
                )
                result = ""
                payload = error
                force_final = True
                log.warning(error)
            else:
                cached_tool = station.get_tool_cache(name, args)
                if cached_tool is not None:
                    result = cached_tool
                    error = ""
                else:
                    try:
                        result = await tools.execute(_effective_tool(name, args, continue_path), args)
                        error = ""
                        if result:
                            station.set_tool_cache(name, args, result)
                    except Exception as exc:  # noqa: BLE001 - tool errors go back to the model
                        result = ""
                        error = str(exc)[:1200]
                        log.warning("tool %s failed: %s", name, exc)
                payload = (error + "\n" + result).strip() if error else result

            if not error and result:
                progress = True
            if error:
                ekey = f"{name}:{error[:160]}"
                seen_errors[ekey] = seen_errors.get(ekey, 0) + 1

            messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": payload})
            await emit(
                {
                    "type": "tool_result",
                    "step": i,
                    "name": name,
                    "arguments": args,
                    "result": payload,
                    "error": error,
                }
            )

        # Creative continuation: the chapter file is still too short -> write more.
        if continue_path and continuations < max_continuations and i < max_steps - 1:
            cur = _file_chars(continue_path)
            if 0 < cur < continue_min_chars:
                continuations += 1
                messages.append(
                    {"role": "user", "content": _continuation_msg(cur, continue_min_chars)}
                )
                force_final = False
                continue

        # Model is looping on the same tool: stop and ask for a plain-text answer.
        if force_final:
            answer = await _final_answer(model, messages, final_max_tokens)
            final = answer or content
            return {
                "answer": final,
                "steps": steps,
                "truncated": not final.strip(),
            }

        # Loop / no-progress nudges
        if not progress and nudges < 3 and i < max_steps - 1:
            nudges += 1
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "No progress (errors or repeated calls). Change approach: inspect actual paths with list_dir, "
                        "correct arguments, or if you have enough info provide the final answer in plain text."
                    ),
                }
            )
        elif progress:
            nudges = max(0, nudges - 1)

        # hard stop on obvious spin
        max_rep = max(seen_calls.values()) if seen_calls else 0
        max_err = max(seen_errors.values()) if seen_errors else 0
        if (max_rep >= 4 or max_err >= 4) and i < max_steps - 1:
            messages.append(
                {
                    "role": "user",
                    "content": "Repeated failures/loops detected. Stop calling tools and provide the best final answer now from available information.",
                }
            )

    # exhausted steps: ask for final answer once with larger budget if possible
    try:
        resp = await llm.chat(model, messages + [{"role": "user", "content": "Step limit reached. Provide final answer now in plain text."}], max_tokens=final_max_tokens)
        choice = resp.get("choices", [{}])[0]
        content = ((choice.get("message") or {}).get("content") or "").strip()
        if content:
            return {"answer": content, "steps": steps, "truncated": True}
    except Exception:  # noqa: BLE001
        pass
    # Last resort: never return a silent empty answer - surface whatever progress exists.
    return {"answer": _best_effort_summary(steps, messages), "steps": steps, "truncated": True}


def _best_effort_summary(steps: list[dict], messages: list[dict]) -> str:
    """Scrape the last useful text out of a failed run instead of returning ''."""
    for step in reversed(steps):
        content = (step.get("content") or "").strip()
        if content and not _only_tool_json(content):
            return f"(partial - step limit reached)\n\n{content}"
    for msg in reversed(messages):
        if msg.get("role") == "tool":
            result = str(msg.get("content") or "").strip()
            if result:
                return f"(partial - step limit reached, last tool result from {msg.get('name')})\n\n{result[:2000]}"
    return ""
