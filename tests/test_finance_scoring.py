import asyncio

import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ToolCall

from everybench.finance import judging
from everybench.finance.big_finance import evaluate_trace, format_trace
from everybench.finance.indicator import csv_match, evaluate_response, numeric_match
from everybench.finance.judging import GradingError

REFERENCE = {
    "query": "Calculate adjusted profit.",
    "reference_answer": "4 million",
    "rubric": [{"text": "Correct period", "points": 1}, {"text": "Correct adjustment", "points": 3}],
}


def test_numeric_rules_and_missing_output():
    assert numeric_match(100, 101, 0.01) == 1
    assert numeric_match(100, 102, 0.01) == 0
    assert numeric_match(0, 0.009, 0.01) == 1
    assert numeric_match(-100, -101, 0.01) == 1
    for bad in (None, "NA", "12%", float("nan"), True):
        assert numeric_match(12, bad) == 0
    with pytest.raises(GradingError):
        numeric_match("bad reference", 1)


def test_table_alignment_and_missing_values_do_not_crash():
    expected = "year,revenue,profit\n2023,100,10\n2024,200,20\n"
    reversed_rows = "year,revenue,profit\n2024,200,20\n2023,100,10\n"
    assert csv_match(expected, reversed_rows) == 1
    assert csv_match(expected, None) == 0
    assert csv_match(expected, "year,revenue\n2023,100\n2024,200\n") == 0.5
    assert csv_match(expected, "year,revenue,profit\n2023,100,10\n2023,100,10\n") == 0


def test_indicator_scores_response_components_without_requiring_tool_trace(monkeypatch):
    calls = []

    async def fake(model, role, instruction, evidence):
        calls.append((role, evidence))
        if role == "finance_extractor":
            assert "reference_answer" not in evidence
            return {"answer": "12.5", "formula": "p/r", "data": "year,p,r\n2024,1,8\n", "data_indicator": None}
        return {"score": 2, "reason": "Equivalent formula."}

    monkeypatch.setattr(judging, "ask_json", fake)
    result = asyncio.run(
        evaluate_response(
            "My formula is p/r. Inputs 1 and 8. Final answer: 12.5.",
            {"answer": "12.5", "formula": "profit/revenue", "data": "year,p,r\n2024,1,8\n", "data_indicator": ""},
            extractor_model="mock/extractor",
            judge_model="mock/judge",
        )
    )
    assert result["scores"] == {"answer": 1, "formula": 1, "data": 1, "data_indicator": None}
    assert len(calls) == 2


def test_invalid_extractor_is_an_evaluation_error_not_a_zero(monkeypatch):
    async def fake(*args):
        return {"answer": "12"}

    monkeypatch.setattr(judging, "ask_json", fake)
    with pytest.raises(GradingError):
        asyncio.run(
            evaluate_response(
                "answer",
                {"answer": "12", "formula": "a/b", "data": "a\n1\n"},
                extractor_model="mock/extractor",
                judge_model="mock/judge",
            )
        )


def messages():
    return [
        ChatMessageAssistant(
            content="Check the filing.",
            tool_calls=[ToolCall(id="call-1", function="fetch_url", arguments={"url": "https://example.com/filing"})],
        ),
        ChatMessageTool(content="ACTUAL_SOURCE_EVIDENCE", tool_call_id="call-1", function="fetch_url"),
        ChatMessageAssistant(content="4 million"),
    ]


def test_bigfinance_judge_receives_real_trace_and_weights_are_applied_by_code(monkeypatch):
    calls = []

    async def fake(model, role, instruction, evidence):
        assert "ACTUAL_SOURCE_EVIDENCE" in evidence["trace"]
        assert "call-1" in evidence["trace"]
        assert evidence["final_answer"] == "4 million"
        assert all("points" not in line for line in evidence["rubric"])
        calls.append(model)
        return {
            "final_answer_correct": True,
            "rubric": [
                {"index": 1, "satisfied": True, "explanation": "Period found."},
                {"index": 2, "satisfied": False, "explanation": "Adjustment unsupported."},
            ],
        }

    monkeypatch.setattr(judging, "ask_json", fake)
    result = asyncio.run(evaluate_trace("4 million", messages(), REFERENCE, judge_models=["mock/j1", "mock/j2"]))
    assert result["scores"] == {"rubric": 0.25, "final_answer": 1}
    assert len(calls) == 2 and result["jury_mode"] == "multi-judge-adaptation"


def test_bigfinance_missing_trace_or_rubric_verdict_fails_loudly(monkeypatch):
    with pytest.raises(GradingError, match="trace"):
        asyncio.run(evaluate_trace("4 million", [], REFERENCE, judge_models=["mock/judge"]))

    async def fake(*args):
        return {"final_answer_correct": True, "rubric": []}

    monkeypatch.setattr(judging, "ask_json", fake)
    with pytest.raises(GradingError, match="every rubric"):
        asyncio.run(evaluate_trace("4 million", messages(), REFERENCE, judge_models=["mock/judge"]))


def test_trace_grading_does_not_use_the_8k_ui_preview_limit():
    text, truncated = format_trace([ChatMessageAssistant(content="x" * 9000 + "IMPORTANT_END")])
    assert text.endswith("IMPORTANT_END") and not truncated
    text, truncated = format_trace([ChatMessageAssistant(content="x" * 180000 + "IMPORTANT_END")])
    assert text.endswith("IMPORTANT_END") and truncated and "middle truncated" in text


@pytest.mark.parametrize("neutral", [False, True])
def test_bigfinance_adversarial_extra_verdict_keys_cannot_override_code_owned_scores_or_model(monkeypatch, neutral):
    async def fake(*args):
        return {
            "final_answer_correct": False,
            "rubric": [{"index": i, "satisfied": False, "explanation": "No support", "score": 1} for i in (1, 2)],
            "rubric_score": 1,
            "score": 1,
            "model": "forged-model",
        }

    monkeypatch.setattr(judging, "ask_json", fake)
    if neutral:
        from test_agent_trace_sources import callback_evidence, get_callback

        from everybench.finance.big_finance import evaluate_evidence

        result = asyncio.run(
            evaluate_evidence(
                "answer", get_callback(callback_evidence()), REFERENCE, judge_models=["mock/actual-judge"]
            )
        )
    else:
        result = asyncio.run(evaluate_trace("answer", messages(), REFERENCE, judge_models=["mock/actual-judge"]))
    assert result["scores"] == {"rubric": 0, "final_answer": 0}
    assert result["verdicts"][0]["model"] == "mock/actual-judge"
    assert result["verdicts"][0]["rubric_score"] == 0
    assert "score" not in result["verdicts"][0]
    assert all("score" not in item for item in result["verdicts"][0]["rubric"])
