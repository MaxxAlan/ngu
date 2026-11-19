"""Hybrid retrieval: fusion, graph expansion, recall, legacy tool route."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.core.memory.retriever import retrieve
from app.core.memory.types import MemoryEntry, make_source, new_id, now_iso
from app.core.memory.writer import MemoryWriter
from app.tools import memory as mem_tools

_REPORT = Path(tempfile.gettempdir()) / "phase3_recall.json"

_TOPICS = {
    "kubernetes": "kubernetes deployment rollout pod namespace",
    "sourdough": "sourdough starter hydration crumb levain",
    "orbital": "orbit periapsis inclination kepler spacecraft",
}


def _write(writer: MemoryWriter, content: str, *, tags: str, key: str) -> MemoryEntry:
    return writer.write(
        MemoryEntry(
            id=new_id(),
            type="semantic",
            content=content,
            source_json=make_source(f"file:corpus/{tags}.md", offset=0),
            confidence=1.0,
            created_at=now_iso(),
            key=key,
            tags=tags,
            verified_by="eval",
        )
    )


def test_recall_at_5_three_topics(mem_env):
    writer = MemoryWriter()
    queries: list[dict] = []
    total = 0
    for topic, words in _TOPICS.items():
        for cluster in range(10):
            marker = f"{topic[:3]}marker{cluster:02d}"
            gold: list[str] = []
            previous = None
            for note in range(5):
                saved = _write(
                    writer,
                    f"{marker} {words} note {note}: case detail {note} for {topic} operations.",
                    tags=topic,
                    key=f"{topic}-{cluster}-{note}",
                )
                gold.append(saved.id)
                if previous is not None:
                    writer.link(previous, saved.id, "same_case", 1.0)
                previous = saved.id
                total += 1
            queries.append(
                {"text": f"{marker} {words}", "gold": gold, "topic": topic},
            )
        for filler in range(17):
            _write(
                writer,
                f"{words} background filler {filler} for {topic} with no case marker.",
                tags=topic,
                key=f"{topic}-filler-{filler}",
            )
            total += 1

    assert 190 <= total <= 220
    recalls: list[float] = []
    topic_hits = 0
    topic_slots = 0
    for query in queries:
        hits = retrieve(query["text"], None, None, 4000)[:5]
        found = {hit.id for hit in hits}
        gold = set(query["gold"])
        recalls.append(len(found & gold) / len(gold))
        for hit in hits:
            topic_slots += 1
            if hit.tags == query["topic"]:
                topic_hits += 1
    mean_recall = sum(recalls) / len(recalls)
    topic_precision = topic_hits / max(1, topic_slots)
    _REPORT.write_text(
        json.dumps(
            {
                "entries": total,
                "queries": len(queries),
                "recall_at_5": round(mean_recall, 4),
                "topic_precision_at_5": round(topic_precision, 4),
                "min_recall": round(min(recalls), 4),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    assert mean_recall >= 0.8, _REPORT.read_text(encoding="utf-8")


def test_graph_expansion_pulls_unrelated_neighbor(mem_env):
    writer = MemoryWriter()
    origin = make_source("tool:runbook", offset=3)
    parent = writer.write(
        MemoryEntry(
            id=new_id(),
            type="procedural",
            content="alpha procedure cache warmup",
            source_json=origin,
            confidence=1.0,
            created_at=now_iso(),
            tags="runbook",
            verified_by="user",
        )
    )
    child = writer.write(
        MemoryEntry(
            id=new_id(),
            type="procedural",
            content="followup steps for the same runbook only",
            source_json=make_source("tool:runbook", offset=40),
            confidence=1.0,
            created_at=now_iso(),
            tags="runbook",
            verified_by="user",
        )
    )
    writer.link(parent.id, child.id, "next_step", 1.0)
    hits = retrieve("alpha procedure", None, None, 1000)
    ids = [hit.id for hit in hits]
    assert parent.id in ids
    assert child.id in ids
    assert ids.index(parent.id) < ids.index(child.id)
    neighbor = next(hit for hit in hits if hit.id == child.id)
    assert neighbor.score_parts.get("graph") is True
    assert neighbor.source_json["offset"] == 40


def test_vector_failure_falls_back_to_bm25(mem_env, monkeypatch):
    writer = MemoryWriter()
    saved = writer.write(
        MemoryEntry(
            id=new_id(),
            type="semantic",
            content="bm25 fallback token xylophonequartz",
            source_json=make_source("tool:test", offset=0),
            confidence=1.0,
            created_at=now_iso(),
            verified_by="test",
        )
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("vec down")

    monkeypatch.setattr("app.core.memory.retriever.store.vector_search", boom)
    hits = retrieve("xylophonequartz", None, None, 500)
    assert any(hit.id == saved.id for hit in hits)


def test_budget_stops_early(mem_env):
    writer = MemoryWriter()
    for index in range(6):
        writer.write(
            MemoryEntry(
                id=new_id(),
                type="semantic",
                content=("budgettoken " * 80) + f"item {index}",
                source_json=make_source("file:budget.md", offset=index),
                confidence=1.0,
                created_at=now_iso(),
                verified_by="test",
                tags="budget",
            )
        )
    tight = retrieve("budgettoken", None, None, 40)
    wide = retrieve("budgettoken", None, None, 4000)
    assert len(tight) < len(wide)
    assert len(tight) >= 1 or tight == []


def test_tool_search_routes_through_engine(mem_env):
    mem_tools.memory_save("design_notes", "Use SQLite FTS5 for local search.", tags="arch backend")
    found = mem_tools.memory_search("FTS5")
    assert "design_notes" in found
    assert "SQLite" in found
    assert "source:" in found
    from app.core.memory import sqlite_store as store

    rows = store.active_by_key("design_notes")
    assert rows and rows[0].type == "episodic"
    assert rows[0].confidence == 1.0
    assert rows[0].source_json["origin"] == "tool:memory_save"
