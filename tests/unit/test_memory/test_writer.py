"""Write policy: confidence gate, supersede, expire."""

from __future__ import annotations

import pytest

from app.core.memory.retriever import retrieve
from app.core.memory.types import MemoryEntry, make_source, new_id, now_iso
from app.core.memory.writer import MemoryWriteError, MemoryWriter


def _entry(content: str, **kwargs) -> MemoryEntry:
    return MemoryEntry(
        id=kwargs.pop("id", new_id()),
        type=kwargs.pop("type", "semantic"),
        content=content,
        source_json=kwargs.pop("source_json", make_source("tool:test", offset=0)),
        confidence=kwargs.pop("confidence", 0.9),
        created_at=kwargs.pop("created_at", now_iso()),
        **kwargs,
    )


def test_rejects_unverified_guess(mem_env):
    writer = MemoryWriter()
    with pytest.raises(MemoryWriteError, match="Model guesses"):
        writer.write(_entry("the sky is green on tuesdays", confidence=0.4))


def test_accepts_verified_low_confidence_and_high_confidence(mem_env):
    writer = MemoryWriter()
    low = writer.write(_entry("checked by a human", confidence=0.4, verified_by="human"))
    assert low.verified_by == "human"
    high = writer.write(_entry("stable fact about sqlite wal", confidence=0.7))
    assert high.confidence == 0.7
    assert high.source_json["origin"] == "tool:test"
    assert "offset" in high.source_json
    assert high.source_json["timestamp"]


def test_provenance_required(mem_env):
    writer = MemoryWriter()
    with pytest.raises(MemoryWriteError, match="origin"):
        writer.write(_entry("missing provenance", source_json={"offset": 0}))


def test_supersede_hides_old_entry(mem_env):
    writer = MemoryWriter()
    old = writer.write(_entry("alpha cache warmup procedure v1", verified_by="user"))
    new = writer.write(_entry("alpha cache warmup procedure v2", verified_by="user"))
    writer.supersede(old.id, new.id)
    hits = retrieve("alpha cache warmup procedure", None, None, 1000)
    ids = [hit.id for hit in hits]
    assert new.id in ids
    assert old.id not in ids
    assert hits[0].score is not None
    assert hits[0].source_json["origin"]


def test_expire_hides_time_sensitive_fact(mem_env):
    writer = MemoryWriter()
    fact = writer.write(_entry("market price of wheat is 9", verified_by="ticker"))
    writer.expire(fact.id)
    hits = retrieve("market price of wheat", None, None, 1000)
    assert all(hit.id != fact.id for hit in hits)
