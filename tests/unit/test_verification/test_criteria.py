"""Unit tests for each acceptance criterion type (positive + negative)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.verification import Verifier, evaluate_criteria, normalize_criteria


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def verifier(ws: Path) -> Verifier:
    return Verifier(workspace=ws, shell_timeout=10)


# ---- normalize ------------------------------------------------------------


def test_normalize_list():
    raw = [{"type": "file_exists", "path": "a.txt"}]
    assert normalize_criteria(raw) == raw


def test_normalize_criteria_key():
    raw = {"criteria": [{"type": "file_exists", "path": "a.txt"}]}
    assert len(normalize_criteria(raw)) == 1


def test_normalize_empty():
    assert normalize_criteria({}) == []
    assert normalize_criteria(None) == []
    assert normalize_criteria("nope") == []


# ---- file_exists ----------------------------------------------------------


def test_file_exists_pass(verifier: Verifier, ws: Path):
    (ws / "ok.txt").write_text("x", encoding="utf-8")
    r = verifier.evaluate({"type": "file_exists", "path": "ok.txt"})
    assert r.passed
    assert "yes" in r.evidence


def test_file_exists_fail(verifier: Verifier):
    r = verifier.evaluate({"type": "file_exists", "path": "missing.txt"})
    assert not r.passed
    assert "no" in r.evidence


# ---- file_contains --------------------------------------------------------


def test_file_contains_pass(verifier: Verifier, ws: Path):
    (ws / "body.txt").write_text("hello world", encoding="utf-8")
    r = verifier.evaluate({"type": "file_contains", "path": "body.txt", "needle": "world"})
    assert r.passed


def test_file_contains_fail_missing_needle(verifier: Verifier, ws: Path):
    (ws / "body.txt").write_text("hello world", encoding="utf-8")
    r = verifier.evaluate({"type": "file_contains", "path": "body.txt", "needle": "xyz"})
    assert not r.passed


def test_file_contains_fail_missing_file(verifier: Verifier):
    r = verifier.evaluate({"type": "file_contains", "path": "gone.txt", "needle": "x"})
    assert not r.passed


# ---- shell_exit_0 ---------------------------------------------------------


def test_shell_exit_0_pass(verifier: Verifier):
    # Cross-platform: `true`/`false` do not exist on Windows cmd.exe
    r = verifier.evaluate({"type": "shell_exit_0", "cmd": 'python -c "raise SystemExit(0)"'})
    assert r.passed
    assert "exit=0" in r.evidence


def test_shell_exit_0_fail(verifier: Verifier):
    r = verifier.evaluate({"type": "shell_exit_0", "cmd": 'python -c "raise SystemExit(1)"'})
    assert not r.passed


# ---- test_passes ----------------------------------------------------------


def test_test_passes_pass(verifier: Verifier):
    r = verifier.evaluate({"type": "test_passes", "cmd": 'python -c "assert 1+1==2"'})
    assert r.passed


def test_test_passes_fail(verifier: Verifier):
    r = verifier.evaluate({"type": "test_passes", "cmd": 'python -c "assert 1+1==3"'})
    assert not r.passed
    assert "exit=" in r.evidence


# ---- llm_judge ------------------------------------------------------------


def test_llm_judge_pass_with_stub(ws: Path):
    def stub(prompt: str, *, model: str | None = None):
        return True, "looks complete"

    v = Verifier(workspace=ws, llm_callable=stub)
    r = v.evaluate({"type": "llm_judge", "prompt": "Is the deliverable done?"})
    assert r.passed
    assert "looks complete" in r.evidence


def test_llm_judge_fail_with_stub(ws: Path):
    def stub(prompt: str, *, model: str | None = None):
        return False, "missing unit tests"

    v = Verifier(workspace=ws, llm_callable=stub)
    r = v.evaluate({"type": "llm_judge", "prompt": "Is the deliverable done?"})
    assert not r.passed
    assert "missing unit tests" in r.evidence


def test_llm_judge_missing_prompt(verifier: Verifier):
    r = verifier.evaluate({"type": "llm_judge"})
    assert not r.passed


# ---- batch evaluate -------------------------------------------------------


def test_evaluate_criteria_all_pass(ws: Path):
    (ws / "a.txt").write_text("ok", encoding="utf-8")
    ok, results = evaluate_criteria(
        [
            {"type": "file_exists", "path": "a.txt"},
            {"type": "shell_exit_0", "cmd": 'python -c "raise SystemExit(0)"'},
        ],
        workspace=ws,
    )
    assert ok
    assert len(results) == 2


def test_evaluate_criteria_one_fail(ws: Path):
    ok, results = evaluate_criteria(
        [
            {"type": "file_exists", "path": "nope.txt"},
            {"type": "shell_exit_0", "cmd": 'python -c "raise SystemExit(0)"'},
        ],
        workspace=ws,
    )
    assert not ok
    assert any(not r.passed for r in results)


def test_unknown_type(verifier: Verifier):
    r = verifier.evaluate({"type": "magic_unicorn"})
    assert not r.passed
