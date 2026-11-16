"""Phase 0 gate: catalog integrity + reference baseline must stay green."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.benchmarks.harness import (
    BASELINE_DIR,
    CATALOG_PATH,
    catalog_sha256,
    compare_to_baseline,
    load_catalog,
    run_suite,
)

ROOT = Path(__file__).resolve().parents[2]


def test_catalog_has_20_to_30_fixed_tasks():
    cat = load_catalog()
    tasks = cat["tasks"]
    assert 20 <= len(tasks) <= 30
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "duplicate task ids"
    for t in tasks:
        assert t.get("prompt")
        assert t.get("category")
        assert t.get("reference_plan"), f"{t['id']} missing reference_plan"
        assert t.get("checks"), f"{t['id']} missing checks"


def test_catalog_categories_cover_phase0_failure_modes():
    cats = {t["category"] for t in load_catalog()["tasks"]}
    required = {
        "tool_validity",
        "task_completion",
        "planning",
        "recovery",
        "constraints",
        "false_completion",
        "efficiency",
        "memory",
    }
    assert required.issubset(cats), f"missing categories: {required - cats}"


@pytest.mark.asyncio
async def test_reference_suite_matches_baseline(tmp_path, monkeypatch):
    """Gate: reference mode is fully deterministic and must match committed baseline."""
    from app import config
    from app import tools as tools_mod

    monkeypatch.setattr(config, "AGENT_ALLOW_ABS", False)
    monkeypatch.setattr(config, "ASK_PERMISSION", False)

    suite = await run_suite(mode="reference", model="reference", work_root=tmp_path / "bench")
    tools_mod.set_workspace(ROOT)

    assert suite.errors == 0, [t.task_id for t in suite.tasks if t.outcome == "error"]
    failed = [t for t in suite.tasks if t.outcome != "pass"]
    assert not failed, [(t.task_id, t.checks, t.error) for t in failed]

    baseline_path = BASELINE_DIR / "reference_results.json"
    assert baseline_path.is_file(), "commit benchmarks/baseline/reference_results.json"
    problems = compare_to_baseline(suite, baseline_path)
    assert not problems, problems

    # catalog hash recorded in baseline must match file on disk
    assert suite.catalog_sha256 == catalog_sha256()
    base = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert base["catalog_sha256"] == catalog_sha256(CATALOG_PATH)
