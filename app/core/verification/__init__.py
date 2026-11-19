"""Acceptance-criteria verification (doer/verifier separation)."""

from app.core.verification.verifier import (
    VERIFIER_SYSTEM_PROMPT,
    CriterionResult,
    Verifier,
    evaluate_criteria,
    normalize_criteria,
)

__all__ = [
    "VERIFIER_SYSTEM_PROMPT",
    "CriterionResult",
    "Verifier",
    "evaluate_criteria",
    "normalize_criteria",
]
