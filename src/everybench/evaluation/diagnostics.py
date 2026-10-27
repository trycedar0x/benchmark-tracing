"""Bounded grading diagnostics.

This module must stay free of provider clients and case content. Messages are an
allowlist so a grader cannot persist prompts, answers, references, or provider bodies.
"""

from __future__ import annotations

from typing import Any

GRADING_STAGES = frozenset({"provider", "timeout", "json_parse", "json_schema", "evidence", "configuration"})

SAFE_GRADING_MESSAGES = frozenset(
    {
        "Evaluator model returned an error.",
        "Evaluator model timed out.",
        "Evaluator did not return valid JSON.",
        "Evaluator JSON response was truncated.",
        "Evaluator response must be a JSON object.",
        "BigFinance grading requires an actual assistant execution trace.",
        "Missing reference rubric.",
        "Invalid rubric text.",
        "Rubric weights must be positive and finite.",
        "BigFinance grading requires complete visible execution evidence.",
        "Choose explicit judge model snapshots.",
        "Repeated copies of a judge are not an independent jury.",
        "Judge verdict is missing final-answer or rubric results.",
        "Rubric verdict must be an object.",
        "Duplicate or invalid rubric index.",
        "Invalid rubric judgment.",
        "Judge did not grade every rubric criterion exactly once.",
        "Invalid numeric reference.",
        "Invalid reference table.",
        "Reference table is empty.",
        "Extractor returned an invalid answer field.",
        "Extractor returned an invalid formula field.",
        "Extractor returned an invalid data field.",
        "Extractor returned an invalid data_indicator field.",
        "Formula judge score must be 0, 1 or 2.",
        "Formula judge must explain its verdict.",
    }
)

_EXCERPT_LIMIT = 24_000


class GradingError(ValueError):
    """Invalid evaluator output is an evaluation failure, not a model's ordinary zero score."""

    def __init__(self, message: str, *, stage: str, invalid_output: str | None = None) -> None:
        if stage not in GRADING_STAGES:
            raise ValueError("Unknown grading diagnostic stage.")
        if message not in SAFE_GRADING_MESSAGES:
            raise ValueError("Unguarded grading diagnostic message.")
        super().__init__(message)
        self.stage = stage
        self.repair_attempted = False
        # Held for one in-memory repair and a redacted excerpt. Never copy this onto a report.
        if isinstance(invalid_output, str) and invalid_output:
            self.invalid_output: str | None = invalid_output[:_EXCERPT_LIMIT]
        else:
            self.invalid_output = None


def bounded_diagnostic(exc: BaseException) -> dict[str, Any]:
    """Stage, exception class, and allowlisted message. No raw output or cause chain."""
    if isinstance(exc, GradingError):
        message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
        repair = exc.repair_attempted if isinstance(exc.repair_attempted, bool) else False
        if exc.stage in GRADING_STAGES and message in SAFE_GRADING_MESSAGES:
            return {
                "stage": exc.stage,
                "exception": "GradingError",
                "message": message,
                "repair_attempted": repair,
            }
    if isinstance(exc, TimeoutError):
        return {
            "stage": "timeout",
            "exception": "TimeoutError",
            "message": "Grading timed out.",
            "repair_attempted": False,
        }
    name = type(exc).__name__
    if not name.isidentifier() or len(name) > 64:
        name = "Exception"
    return {"stage": "unspecified", "exception": name, "message": "Grading failed.", "repair_attempted": False}
