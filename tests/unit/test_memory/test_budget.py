"""Token budgeting replaces character budgets in the buffer station."""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path

import tiktoken

from app import config
from app.buffer_station import AIBufferStation
from app.core.context import budget

_REPORT = Path("/tmp/phase3_context.json")


def test_tiktoken_matches_library():
    text = "Hierarchical memory keeps provenance with every fact."
    encoder = tiktoken.get_encoding("cl100k_base")
    assert budget.count_tokens(text, "cl100k_base") == len(encoder.encode(text))
    assert budget.count_tokens(text, "gpt-4") == len(encoder.encode(text))


def test_missing_tokenizer_logs_loudly(caplog):
    budget._warned.clear()
    caplog.set_level(logging.WARNING)
    count = budget.count_tokens("hello world from the heuristic", "not-a-real-tokenizer")
    assert count >= 1
    assert any("LOUD FALLBACK" in record.message for record in caplog.records)


def test_compress_does_not_budget_with_len():
    source = inspect.getsource(AIBufferStation.compress_messages_for_vram)
    assert "len(" not in source


def test_token_strategy_truncates_old_tool_output(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_STRATEGY", "token")
    monkeypatch.setattr(config, "CONTEXT_TOKENIZER", "cl100k_base")
    station = AIBufferStation(cache_file=Path("/tmp/phase3-station-cache.json"))
    fat = "tool output line\n" * 400
    messages = [
        {"role": "system", "content": "You are ngu."},
        {"role": "user", "content": "old question that should be dropped"},
        {"role": "tool", "content": fat},
        {"role": "assistant", "content": "recent a"},
        {"role": "tool", "content": "recent b"},
        {"role": "user", "content": "recent c"},
    ]
    compressed = station.compress_messages_for_vram(messages, max_tokens=32)
    roles = [msg.get("role") for msg in compressed]
    assert "old question that should be dropped" not in json.dumps(compressed)
    assert roles[-3:] == ["assistant", "tool", "user"]
    tool_msgs = [msg["content"] for msg in compressed if msg.get("role") == "tool"]
    assert any("compressed older tool output" in text for text in tool_msgs)


def test_context_size_before_after_fs04(monkeypatch):
    """Replay benchmark task fs04_edit_file and compare char vs token budgets.

    The historical call site is ``max_total_chars=max(4000, ctx * 3)``. With the
    4096-token window the buffer station was written for, that is 12288 characters
    or 3072 tokens.
    """
    monkeypatch.setattr(config, "CONTEXT_TOKENIZER", "cl100k_base")
    station = AIBufferStation(cache_file=Path("/tmp/phase3-station-fs04.json"))
    ctx = 4096
    char_budget = max(4000, ctx * 3)
    system = (
        "You are a local coding agent. Read config.txt, replace OLD with NEW, "
        "and report the result. Keep tool output in the transcript."
    )
    user = "In config.txt replace OLD with NEW using an exact edit."
    read_body = "value=OLD\n" + ("# padding so the tool log is large\n" * 180)
    edit_body = "edited config.txt\n" + ("status ok\n" * 180)
    steps = [
        [],
        [
            {"role": "assistant", "content": "I will read config.txt first."},
            {"role": "tool", "content": read_body},
        ],
        [
            {"role": "assistant", "content": "Now edit OLD to NEW."},
            {"role": "tool", "content": edit_body},
        ],
        [
            {"role": "assistant", "content": "Verifying the file."},
            {"role": "tool", "content": "value=NEW\n" + ("confirm\n" * 180)},
        ],
        [
            {"role": "assistant", "content": "The replacement is done. config.txt now contains NEW."},
        ],
    ]
    rows = []
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    for index, extra in enumerate(steps):
        messages = messages + extra
        monkeypatch.setattr(config, "CONTEXT_BUDGET_STRATEGY", "char")
        char_out = station.compress_messages_for_vram([dict(m) for m in messages], max_total_chars=char_budget)
        monkeypatch.setattr(config, "CONTEXT_BUDGET_STRATEGY", "token")
        token_out = station.compress_messages_for_vram(
            [dict(m) for m in messages],
            max_total_chars=char_budget,
        )
        rows.append(
            {
                "step": index,
                "raw_chars": _chars(messages),
                "raw_tokens": _tokens(messages),
                "char_strategy_chars": _chars(char_out),
                "char_strategy_tokens": _tokens(char_out),
                "token_strategy_chars": _chars(token_out),
                "token_strategy_tokens": _tokens(token_out),
            }
        )
    _REPORT.write_text(
        json.dumps(
            {
                "task": "fs04_edit_file",
                "ctx": ctx,
                "char_budget": char_budget,
                "token_budget": char_budget // 4,
                "steps": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # Token strategy must actually consult the tokenizer (counts differ from raw chars).
    assert rows[-1]["token_strategy_tokens"] > 0
    assert rows[-1]["token_strategy_tokens"] != rows[-1]["token_strategy_chars"]


def _chars(messages: list[dict]) -> int:
    return sum(budget.count_chars(str(msg.get("content") or "")) for msg in messages)


def _tokens(messages: list[dict]) -> int:
    return sum(budget.count_tokens(str(msg.get("content") or ""), "cl100k_base") for msg in messages)
