"""Procedures track how often they succeed."""

from __future__ import annotations

import pytest

from app.core.memory.procedural import ProceduralMemory
from app.core.memory.types import make_source


def test_outcome_counts(mem_env):
    mem = ProceduralMemory()
    proc = mem.add(
        "rollout",
        ["build image", "deploy canary", "promote"],
        source=make_source("tool:runbook", offset=0),
        verified_by="user",
    )
    assert proc.success_count == 0
    proc = mem.record_outcome(proc.id, success=True)
    proc = mem.record_outcome(proc.id, success=True)
    proc = mem.record_outcome(proc.id, success=False)
    assert proc.success_count == 2
    assert proc.failure_count == 1
    assert mem.success_rate(proc) == pytest.approx(2 / 3)
    assert "deploy canary" in proc.content


def test_outcome_rejects_non_procedure(mem_env):
    from app.core.memory.semantic import SemanticMemory

    fact = SemanticMemory().add_fact(
        "not a procedure",
        source=make_source("tool:test", offset=0),
        verified_by="user",
        confidence=1.0,
    )
    with pytest.raises(ValueError):
        ProceduralMemory().record_outcome(fact.id, success=True)
