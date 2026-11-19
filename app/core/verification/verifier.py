"""Deterministic + optional LLM acceptance-criteria evaluator.

DSL criterion shapes (JSON objects):
  {"type": "file_exists", "path": "..."}
  {"type": "file_contains", "path": "...", "needle": "..."}
  {"type": "shell_exit_0", "cmd": "..."}
  {"type": "test_passes", "cmd": "..."}
  {"type": "llm_judge", "prompt": "...", "model": "..."}   # model optional
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import config

_log = logging.getLogger("local-agents.verifier")

# Skeptical auditor prompt — used only for llm_judge criteria.
VERIFIER_SYSTEM_PROMPT = (
    "You are an auditor. Your job is to find reasons the deliverable is incomplete "
    "or incorrect. Be skeptical. Do not give the benefit of the doubt. "
    "Respond with a single JSON object: "
    '{"passed": true|false, "reasoning": "<short explanation>"}.'
)

# Shell criteria hard timeout (seconds).
_SHELL_TIMEOUT = 60


@dataclass(frozen=True)
class CriterionResult:
    passed: bool
    evidence: str
    criterion_type: str = ""


def normalize_criteria(acceptance: Any) -> list[dict[str, Any]]:
    """Turn task/subtask acceptance payload into a list of criterion dicts.

    Accepts:
      - list of criterion objects
      - {"criteria": [...]}
      - single criterion dict with a "type" key
      - empty / unknown → []
    """
    if acceptance is None:
        return []
    if isinstance(acceptance, list):
        return [c for c in acceptance if isinstance(c, dict) and c.get("type")]
    if isinstance(acceptance, dict):
        if "criteria" in acceptance and isinstance(acceptance["criteria"], list):
            return [c for c in acceptance["criteria"] if isinstance(c, dict) and c.get("type")]
        if acceptance.get("type"):
            return [acceptance]
    return []


def evaluate_criteria(
    acceptance: Any,
    *,
    workspace: Path | None = None,
    llm_callable: Any | None = None,
) -> tuple[bool, list[CriterionResult]]:
    """Evaluate all criteria. Returns (all_passed, per-criterion results)."""
    v = Verifier(workspace=workspace, llm_callable=llm_callable)
    criteria = normalize_criteria(acceptance)
    if not criteria:
        return True, []
    results = [v.evaluate(c) for c in criteria]
    return all(r.passed for r in results), results


class Verifier:
    """Evaluates one acceptance criterion at a time.

    file_* and shell_* are deterministic. llm_judge includes reasoning in evidence.
    """

    def __init__(
        self,
        *,
        workspace: Path | None = None,
        llm_callable: Any | None = None,
        shell_timeout: float = _SHELL_TIMEOUT,
    ) -> None:
        self.workspace = Path(workspace) if workspace else Path(config.DATA_DIR).parent
        # Prefer explicit workspace from config if available.
        ws = getattr(config, "WORKSPACE_DIR", None) or getattr(config, "BASE_DIR", None)
        if workspace is None and ws is not None:
            self.workspace = Path(ws)
        self.llm_callable = llm_callable
        self.shell_timeout = shell_timeout

    def evaluate(self, criterion: dict[str, Any]) -> CriterionResult:
        ctype = str(criterion.get("type") or "").strip()
        try:
            if ctype == "file_exists":
                return self._file_exists(criterion)
            if ctype == "file_contains":
                return self._file_contains(criterion)
            if ctype == "shell_exit_0":
                return self._shell_exit_0(criterion)
            if ctype == "test_passes":
                return self._test_passes(criterion)
            if ctype == "llm_judge":
                return self._llm_judge(criterion)
            return CriterionResult(
                passed=False,
                evidence=f"unknown criterion type: {ctype!r}",
                criterion_type=ctype or "unknown",
            )
        except Exception as exc:  # noqa: BLE001 - surface as failed criterion
            _log.warning("criterion %s raised: %s", ctype, exc)
            return CriterionResult(
                passed=False,
                evidence=f"evaluator error: {exc}",
                criterion_type=ctype,
            )

    # ------------------------------------------------------------------
    # Deterministic criteria
    # ------------------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        return p

    def _file_exists(self, c: dict[str, Any]) -> CriterionResult:
        path = str(c.get("path") or "")
        if not path:
            return CriterionResult(False, "file_exists: missing path", "file_exists")
        p = self._resolve(path)
        ok = p.exists()
        return CriterionResult(
            ok,
            f"file_exists {p}: {'yes' if ok else 'no'}",
            "file_exists",
        )

    def _file_contains(self, c: dict[str, Any]) -> CriterionResult:
        path = str(c.get("path") or "")
        needle = str(c.get("needle") or "")
        if not path:
            return CriterionResult(False, "file_contains: missing path", "file_contains")
        p = self._resolve(path)
        if not p.is_file():
            return CriterionResult(False, f"file_contains: not a file: {p}", "file_contains")
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return CriterionResult(False, f"file_contains: read error: {exc}", "file_contains")
        ok = needle in text
        return CriterionResult(
            ok,
            f"file_contains {p} needle={needle!r}: {'found' if ok else 'missing'}",
            "file_contains",
        )

    def _run_shell(self, cmd: str) -> tuple[int, str]:
        """Sandboxed subprocess with timeout. Returns (exit_code, combined output)."""
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.shell_timeout,
                cwd=str(self.workspace),
                env=None,  # inherit but caller controls cmd
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            return proc.returncode, out.strip()[:4000]
        except subprocess.TimeoutExpired:
            return -1, f"timeout after {self.shell_timeout}s"
        except OSError as exc:
            return -1, f"os error: {exc}"

    def _shell_exit_0(self, c: dict[str, Any]) -> CriterionResult:
        cmd = str(c.get("cmd") or "").strip()
        if not cmd:
            return CriterionResult(False, "shell_exit_0: missing cmd", "shell_exit_0")
        code, out = self._run_shell(cmd)
        ok = code == 0
        return CriterionResult(
            ok,
            f"shell_exit_0 cmd={cmd!r} exit={code} out={out[:500]!r}",
            "shell_exit_0",
        )

    def _test_passes(self, c: dict[str, Any]) -> CriterionResult:
        cmd = str(c.get("cmd") or "").strip()
        if not cmd:
            return CriterionResult(False, "test_passes: missing cmd", "test_passes")
        code, out = self._run_shell(cmd)
        ok = code == 0
        return CriterionResult(
            ok,
            f"test_passes cmd={cmd!r} exit={code} out={out[:800]!r}",
            "test_passes",
        )

    # ------------------------------------------------------------------
    # LLM judge (non-deterministic; reasoning always in evidence)
    # ------------------------------------------------------------------

    def _llm_judge(self, c: dict[str, Any]) -> CriterionResult:
        prompt = str(c.get("prompt") or "").strip()
        if not prompt:
            return CriterionResult(False, "llm_judge: missing prompt", "llm_judge")
        model = str(c.get("model") or "").strip() or getattr(config, "VERIFIER_MODEL", "") or ""

        if self.llm_callable is None:
            # Lazy import to avoid circular deps at module load.
            try:
                from app.core.verification.llm_bridge import judge as _judge

                passed, reasoning = _judge(prompt, model=model or None)
            except Exception as exc:  # noqa: BLE001
                return CriterionResult(
                    False,
                    f"llm_judge unavailable: {exc}",
                    "llm_judge",
                )
        else:
            try:
                passed, reasoning = self.llm_callable(prompt, model=model or None)
            except Exception as exc:  # noqa: BLE001
                return CriterionResult(
                    False,
                    f"llm_judge call failed: {exc}",
                    "llm_judge",
                )

        evidence = f"llm_judge passed={passed}; reasoning: {reasoning}"
        return CriterionResult(bool(passed), evidence, "llm_judge")
