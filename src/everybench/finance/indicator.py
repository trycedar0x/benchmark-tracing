"""Independent evaluator over Apache-2.0 FinDeepIndicator data; no upstream code copied.

This is an explicitly versioned EveryBench adaptation, not certified leaderboard parity.
The evaluator inspects the submitted response, not private model reasoning or tool traces.
"""

from __future__ import annotations

import csv
import io
import math
from typing import Any

from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import TaskState

from everybench.finance import judging
from everybench.finance.judging import GradingError

PROTOCOL = "everybench.findeepindicator.response-v1"


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def numeric_match(reference: Any, candidate: Any, tolerance: float = 0.01) -> float:
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Tolerance must be non-negative and finite.")
    expected, actual = finite_number(reference), finite_number(candidate)
    if expected is None:
        raise GradingError("Invalid numeric reference.", stage="evidence")
    if actual is None:
        return 0.0
    error = abs(actual - expected)
    return float(error <= tolerance if expected == 0 else error / abs(expected) <= tolerance)


def csv_match(reference: str, candidate: str | None, tolerance: float = 0.01) -> float | None:
    """Score numeric reference cells aligned on year/corporate/date, not row order alone."""
    try:
        expected_reader = csv.DictReader(io.StringIO(reference))
        expected = list(expected_reader)
        headers = expected_reader.fieldnames or []
    except csv.Error as ex:
        raise GradingError("Invalid reference table.", stage="evidence") from ex
    if not expected or not headers:
        raise GradingError("Reference table is empty.", stage="evidence")
    indexes = [key for key in ("year", "corporate", "date") if key in headers]
    try:
        actual_rows = list(csv.DictReader(io.StringIO(candidate or "")))
    except csv.Error:
        actual_rows = []

    def key(row: dict[str, str], position: int) -> tuple[str, ...]:
        return tuple(str(row.get(k, "")).strip() for k in indexes) if indexes else (str(position),)

    actual: dict[tuple[str, ...], dict[str, str]] = {}
    duplicates: set[tuple[str, ...]] = set()
    for position, row in enumerate(actual_rows):
        identity = key(row, position)
        if identity in actual:
            duplicates.add(identity)
        actual[identity] = row
    correct, total = 0.0, 0
    for position, row in enumerate(expected):
        identity = key(row, position)
        match = actual.get(identity, {}) if identity not in duplicates else {}
        for column in headers:
            if column in indexes or finite_number(row.get(column)) is None:
                continue
            total += 1
            correct += numeric_match(row[column], match.get(column), tolerance)
    # Some raw-data tables contain no numeric cells. They have no numeric score.
    return correct / total if total else None


async def evaluate_response(
    response: str,
    reference: dict[str, Any],
    *,
    extractor_model: str,
    judge_model: str,
    tolerance: float = 0.01,
) -> dict[str, Any]:
    """Extract declared work from the response, then independently score each component."""
    # Only evaluator calls receive the reference; the task's agent never does.
    extracted = await judging.ask_json(
        extractor_model,
        "finance_extractor",
        "Extract the work explicitly present in the submitted financial answer. Do not solve the question, "
        "invent missing work, convert units, or repair mistakes. Return exactly four fields: "
        "answer (a numeric string, with no units, or null), formula (string or null), "
        "data (raw inputs as CSV or null), data_indicator (computed per-period values as CSV or null). "
        "Use the supplied CSV column names, if applicable. Missing evidence must be null.",
        {
            "response": response,
            "table_columns": {
                key: next(csv.reader(io.StringIO(reference.get(key) or "")), []) for key in ("data", "data_indicator")
            },
        },
    )
    invalid_extractor = {
        "answer": "Extractor returned an invalid answer field.",
        "formula": "Extractor returned an invalid formula field.",
        "data": "Extractor returned an invalid data field.",
        "data_indicator": "Extractor returned an invalid data_indicator field.",
    }
    for field, message in invalid_extractor.items():
        if field not in extracted or (extracted[field] is not None and not isinstance(extracted[field], str)):
            raise GradingError(message, stage="json_schema")
    answer = numeric_match(reference["answer"], extracted["answer"], tolerance)
    formula, formula_reason = 0.0, "No formula was submitted."
    if extracted["formula"]:
        verdict = await judging.ask_json(
            judge_model,
            "finance_formula_judge",
            "Compare a candidate financial formula to its reference. Equivalent notation is allowed. "
            'Return {"score": 0|1|2, "reason": string}: 2 means mathematically and financially equivalent '
            "with the same inputs, 1 means partially correct but with a substantive error, 0 means incorrect. "
            "Do not award credit for a correct final number when the formula itself is wrong.",
            {"reference_formula": reference["formula"], "candidate_formula": extracted["formula"]},
        )
        mark = verdict.get("score")
        if isinstance(mark, bool) or not isinstance(mark, int) or mark not in (0, 1, 2):
            raise GradingError("Formula judge score must be 0, 1 or 2.", stage="json_schema")
        if not isinstance(verdict.get("reason"), str):
            raise GradingError("Formula judge must explain its verdict.", stage="json_schema")
        formula, formula_reason = mark / 2, verdict["reason"]
    return {
        "protocol": PROTOCOL,
        "scores": {
            "answer": answer,
            "formula": formula,
            "data": csv_match(reference["data"], extracted["data"], tolerance),
            "data_indicator": csv_match(reference["data_indicator"], extracted["data_indicator"], tolerance)
            if reference.get("data_indicator", "").strip()
            else None,
        },
        "extracted": extracted,
        "formula_reason": formula_reason,
        "tolerance": tolerance,
        "evaluators": {"extractor": extractor_model, "formula_judge": judge_model},
    }


@scorer(metrics=[mean()])
def indicator_score(extractor_model: str, judge_model: str, tolerance: float = 0.01) -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        evaluation = await evaluate_response(
            state.output.completion,
            state.metadata["finance_reference"],
            extractor_model=extractor_model,
            judge_model=judge_model,
            tolerance=tolerance,
        )
        return Score(
            value=evaluation["scores"]["answer"],
            answer=state.output.completion,
            explanation="Final-answer numeric accuracy; "
            "formula, data and computed-series scores are separate evidence.",
            metadata=evaluation,
        )

    return score
