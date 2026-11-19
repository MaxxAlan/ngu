"""Semantic facts, entities, and relationship links."""

from __future__ import annotations

import pytest

from app.core.memory import sqlite_store as store
from app.core.memory.semantic import SemanticMemory
from app.core.memory.types import make_source
from app.core.memory.writer import MemoryWriteError


def test_fact_links_entity(mem_env):
    mem = SemanticMemory()
    fact = mem.add_fact(
        "sqlite stores durable facts for the agent",
        source=make_source("file:docs/memory.md", offset=12),
        entities=["sqlite"],
        confidence=0.95,
    )
    assert fact.type == "semantic"
    assert fact.source_json["offset"] == 12
    links = store.links_from(fact.id)
    assert any(link["relation"] == "mentions" for link in links)
    entity = store.active_by_key("entity:sqlite")
    assert entity and entity[0].content == "entity sqlite"


def test_guess_is_not_stored(mem_env):
    with pytest.raises(MemoryWriteError):
        SemanticMemory().add_fact(
            "maybe the user likes rust",
            source=make_source("tool:model_guess", offset=0),
            confidence=0.2,
        )
