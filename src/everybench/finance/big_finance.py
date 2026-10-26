"""Trace-aware rubric grading for the licensed BigFinanceBench public-50 subset.

Protocol adapted from Rogo Technologies' Apache-2.0 grader (2026):
https://github.com/Rogo-Technologies/big-finance-benchmark/blob/
3b13eecc4cacb4cfa13ac2b7c111d14ce127dd71/big_finance_harness/grader.py
This independent Inspect integration is versioned as an adaptation, not leaderboard parity.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import TaskState

from everybench.evaluation.evidence import TOOL_ARGUMENT_CAP, TOOL_RESULT_CAP, TRACE_CAP, format_evidence
from everybench.evaluation.types import TraceAccessError, TraceEvidence
from everybench.finance import judging
from everybench.finance.judging import GradingError
from everybench.inspect_convert import message_evidence

PROTOCOL = "everybench.bigfinance-public-50.trace-rubric-v1"


def format_trace(messages: list[Any]) -> tuple[str, bool]:
    """Use actual visible messages, not hidden reasoning or 8k-character UI previews."""
    if not any(message.role == "assistant" for message in messages):
        raise GradingError("BigFinance grading requires an actual assistant execution trace.")
    lines: list[str] = []
    truncated = False
    for message in messages:
        if message.role not in ("assistant", "tool"):
            continue
        evidence = message_evidence(message)
        text = evidence["text"]
        if message.role == "tool" and len(text) > TOOL_RESULT_CAP:
            text = text[:TOOL_RESULT_CAP] + "... [tool result truncated]"
            truncated = True
        lines.append(f"{message.role}: {text}")
        if message.role == "tool":
            lines.append(f"tool_call_id: {evidence.get('tool_call_id', '')}")
        for call in evidence.get("tool_calls", []):
            arguments = json.dumps(call["arguments"], ensure_ascii=False)
            if len(arguments) > TOOL_ARGUMENT_CAP:
                arguments = arguments[:TOOL_ARGUMENT_CAP] + "... [arguments truncated]"
                truncated = True
            lines.append(f"tool_call {call['id']} {call['function']}({arguments})")
        if evidence.get("error"):
            lines.append("tool_error: " + json.dumps(evidence["error"], ensure_ascii=False))
    trace = "\n".join(lines)
    if len(trace) > TRACE_CAP:
        half = TRACE_CAP // 2
        trace = trace[:half] + "\n... [trace middle truncated] ...\n" + trace[-half:]
        truncated = True
    return trace, truncated


def validate_rubric(reference: dict[str, Any]) -> list[dict[str, Any]]:
    rubric = reference.get("rubric")
    if not isinstance(rubric, list) or not rubric:
        raise GradingError("Missing reference rubric.")
    for criterion in rubric:
        points = criterion.get("points")
        if not isinstance(criterion.get("text"), str) or not criterion["text"]:
            raise GradingError("Invalid rubric text.")
        if isinstance(points, bool) or not isinstance(points, int | float) or not math.isfinite(points) or points <= 0:
            raise GradingError("Rubric weights must be positive and finite.")
    return rubric


async def evaluate_trace(
    final_answer: str,
    messages: list[Any],
    reference: dict[str, Any],
    *,
    judge_models: list[str],
) -> dict[str, Any]:
    trace, truncated = format_trace(messages)
    return await _evaluate_formatted(final_answer, trace, truncated, reference, judge_models=judge_models)


async def evaluate_evidence(
    final_answer: str,
    evidence: TraceEvidence,
    reference: dict[str, Any],
    *,
    judge_models: list[str],
) -> dict[str, Any]:
    """Grade captured external events directly; never fabricate Inspect messages."""
    try:
        trace, truncated = format_evidence(evidence)
    except TraceAccessError:
        raise GradingError("BigFinance grading requires complete visible execution evidence.") from None
    return await _evaluate_formatted(final_answer, trace, truncated, reference, judge_models=judge_models)


async def _evaluate_formatted(
    final_answer: str,
    trace: str,
    truncated: bool,
    reference: dict[str, Any],
    *,
    judge_models: list[str],
) -> dict[str, Any]:
    if not judge_models or any(not model.strip() for model in judge_models):
        raise GradingError("Choose explicit judge model snapshots.")
    if len(set(judge_models)) != len(judge_models):
        raise GradingError("Repeated copies of a judge are not an independent jury.")
    rubric = validate_rubric(reference)
    # Weights are deliberately withheld from the judge and applied by code afterwards.
    payload = {
        "question": reference["query"],
        "reference_answer": reference["reference_answer"],
        "rubric": [{"index": i + 1, "text": line["text"]} for i, line in enumerate(rubric)],
        "final_answer": final_answer,
        "trace": trace,
    }
    verdicts = []
    for i, model in enumerate(judge_models):
        verdict = await judging.ask_json(
            model,
            f"finance_rubric_judge_{i + 1}",
            "Evaluate a financial-research agent using its final answer AND actual visible execution trace. "
            "A rubric criterion passes only when positive evidence in the supplied record supports it. "
            "Also separately decide whether the final answer matches the reference, allowing equivalent "
            "numeric formatting but requiring the correct sign and units. Return JSON: "
            '{"final_answer_correct": boolean, "rubric": ['
            '{"index": integer, "satisfied": boolean, "explanation": string}]}. '
            "Return exactly one verdict per numbered criterion; do not invent missing tool evidence.",
            payload,
        )
        if not isinstance(verdict.get("final_answer_correct"), bool) or not isinstance(verdict.get("rubric"), list):
            raise GradingError("Judge verdict is missing final-answer or rubric results.")
        indexed: dict[int, dict[str, Any]] = {}
        for item in verdict["rubric"]:
            if not isinstance(item, dict):
                raise GradingError("Rubric verdict must be an object.")
            index = item.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or index in indexed:
                raise GradingError("Duplicate or invalid rubric index.")
            if not isinstance(item.get("satisfied"), bool) or not isinstance(item.get("explanation"), str):
                raise GradingError("Invalid rubric judgment.")
            indexed[index] = item
        if set(indexed) != set(range(1, len(rubric) + 1)):
            raise GradingError("Judge did not grade every rubric criterion exactly once.")
        total = sum(line["points"] for line in rubric)
        weighted = sum(line["points"] * indexed[i + 1]["satisfied"] for i, line in enumerate(rubric)) / total
        verdicts.append(
            {
                "final_answer_correct": verdict["final_answer_correct"],
                "rubric": [
                    {key: item[key] for key in ("index", "satisfied", "explanation")} for item in verdict["rubric"]
                ],
                "model": model,
                "rubric_score": weighted,
            }
        )
    return {
        "protocol": PROTOCOL,
        "jury_mode": "single-judge-adaptation" if len(verdicts) == 1 else "multi-judge-adaptation",
        "scores": {
            "rubric": sum(v["rubric_score"] for v in verdicts) / len(verdicts),
            "final_answer": sum(v["final_answer_correct"] for v in verdicts) / len(verdicts),
        },
        "verdicts": verdicts,
        "rubric": rubric,
        "trace_sha256": hashlib.sha256(trace.encode()).hexdigest(),
        "trace_truncated_for_judge": truncated,
        "trace_caps": {"total": TRACE_CAP, "tool_result": TOOL_RESULT_CAP, "tool_arguments": TOOL_ARGUMENT_CAP},
    }


@scorer(metrics={"rubric": [mean()], "final_answer": [mean()]})
def big_finance_score(judge_models: list[str]) -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        evaluation = await evaluate_trace(
            state.output.completion,
            state.messages,
            state.metadata["finance_reference"],
            judge_models=judge_models,
        )
        return Score(
            value=evaluation["scores"],
            answer=state.output.completion,
            explanation="Weighted trace-supported rubric score; final-answer correctness is reported separately.",
            metadata=evaluation,
        )

    return score
