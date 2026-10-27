import asyncio
import hashlib

import pytest
from test_agent_trace_sources import callback_evidence, get_callback
from test_finance_scoring import REFERENCE

from everybench.evaluation.evidence import TRACE_CAP, format_evidence
from everybench.evaluation.types import TraceAccessError, TraceEvent, TraceEvidence
from everybench.finance import judging
from everybench.finance.big_finance import PROTOCOL, evaluate_evidence
from everybench.finance.judging import GradingError


def evidence(events):
    return TraceEvidence(trace_id="a" * 32, state="complete", events=events)


def test_evidence_contains_real_ordered_actions_and_useful_identities_not_agent_answer():
    e = callback_evidence()
    e.events[0].content = {"output.value": "THIS ROOT FINAL ANSWER IS NOT A MODEL EVENT"}
    e.events[1].attributes["gen_ai.request.model"] = "actual-model-revision"
    e.events[1].start_time = "2026-01-01T10:00:00+00:00"
    e.events[2].attributes["gen_ai.tool.name"] = "sec_get_observations"
    e.events[2].start_time = "2026-01-01T11:00:00+00:00"
    e.events = list(reversed(e.events))
    text, truncated = format_evidence(get_callback(e))
    assert text.index("actual model step") < text.index("SEC observations")
    assert "actual-model-revision" in text and "sec_get_observations" in text
    assert "revenue" in text and "100" in text
    assert "ROOT FINAL ANSWER" not in text
    assert not truncated


def test_evidence_order_uses_real_timezones_and_stable_source_order():
    first = TraceEvent(
        id="z",
        kind="model",
        name="first",
        start_time="2026-01-01T12:00:00+02:00",
        content={"output.value": "first visible"},
    )
    second = TraceEvent(
        id="a",
        kind="tool",
        name="second",
        start_time="2026-01-01T10:30:00+00:00",
        content={"output.value": "second visible"},
    )
    text, _ = format_evidence(evidence([second, first]))
    assert text.index("first visible") < text.index("second visible")
    first.start_time = second.start_time = None
    text, _ = format_evidence(evidence([first, second]))
    assert text.index("first visible") < text.index("second visible")


def test_visible_model_messages_filter_hidden_reasoning_and_private_provider_fields():
    event = TraceEvent(
        id="model",
        kind="model",
        name="actual model",
        content={
            "gen_ai.output.messages": [
                {"role": "assistant", "channel": "analysis", "content": "PRIVATE_ANALYSIS"},
                {
                    "role": "assistant",
                    "reasoning_content": "PRIVATE_REASONING_FIELD",
                    "parts": [
                        {"type": "reasoning", "content": "PRIVATE_REASONING_PART"},
                        {"type": "text", "content": "VISIBLE_ANSWER"},
                        {"type": "tool_call", "id": "call-1", "name": "SEC", "arguments": {"ticker": "ACME"}},
                    ],
                },
            ],
            "reasoning": "PRIVATE_TOP_LEVEL",
            "events": [{"name": "reasoning", "attributes": {"value": "PRIVATE_EVENT"}}],
        },
    )
    text, truncated = format_evidence(evidence([event]))
    assert "PRIVATE_" not in text and not truncated
    assert "VISIBLE_ANSWER" in text and "call-1" in text and "ACME" in text


@pytest.mark.parametrize(
    "value",
    [
        {"role": "assistant", "content": "VISIBLE", "reasoning_content": "PRIVATE"},
        '[{"role":"assistant","content":[{"type":"text","text":"VISIBLE"},{"type":"reasoning","text":"PRIVATE"}]}]',
        {"text": "VISIBLE", "reasoning": "PRIVATE"},
    ],
)
def test_openinference_model_envelopes_are_not_dumped_with_hidden_reasoning(value):
    text, _ = format_evidence(
        evidence([TraceEvent(id="m", kind="model", name="model", content={"output.value": value})])
    )
    assert "VISIBLE" in text and "PRIVATE" not in text


def test_scorer_models_cannot_be_substituted_even_for_direct_neutral_evidence():
    events = [
        TraceEvent(id="scorer", kind="scorer", name="finance scorer"),
        TraceEvent(
            id="judge", parent_id="scorer", kind="model", name="judge", content={"output.value": "EVALUATOR_ONLY"}
        ),
    ]
    with pytest.raises(TraceAccessError):
        format_evidence(evidence(events))
    events.append(TraceEvent(id="actual", kind="model", name="actual", content={"output.value": "ACTUAL_VISIBLE"}))
    text, _ = format_evidence(evidence(events))
    assert "ACTUAL_VISIBLE" in text and "EVALUATOR_ONLY" not in text
    events[-1].attributes["openinference.span.kind"] = "EVALUATOR"
    with pytest.raises(TraceAccessError):
        format_evidence(evidence(events))


@pytest.mark.parametrize("state", ["pending", "missing", "incomplete", "withheld"])
def test_noncomplete_evidence_must_not_be_graded_as_full_trace(state):
    e = callback_evidence()
    e.state = state
    with pytest.raises(TraceAccessError):
        format_evidence(e)
    with pytest.raises(GradingError) as caught:
        asyncio.run(evaluate_evidence("answer", e, REFERENCE, judge_models=["mock/judge"]))
    assert caught.value.stage == "evidence"


def test_metadata_or_hidden_reasoning_only_is_not_visible_trace():
    for event in [
        TraceEvent(id="agent", kind="agent", name="answer", content={"output.value": "final answer"}),
        TraceEvent(id="model", kind="model", name="metadata only", attributes={"tokens": 100}),
        TraceEvent(
            id="model",
            kind="model",
            name="withheld",
            attributes={"everybench.content_state": "withheld"},
            content={"output.value": "not permitted"},
        ),
        TraceEvent(
            id="model",
            kind="model",
            name="hidden only",
            content={
                "gen_ai.output.messages": [{"role": "assistant", "parts": [{"type": "reasoning", "content": "hidden"}]}]
            },
        ),
    ]:
        with pytest.raises(TraceAccessError):
            format_evidence(evidence([event]))


def test_truncation_preserves_end_and_hashes_with_declared_caps():
    tool_output = "source " + "x" * 9000 + "IMPORTANT_END"
    event = TraceEvent(
        id="t",
        kind="tool",
        name="SEC filing",
        attributes={"gen_ai.tool.name": "SEC"},
        content={
            "gen_ai.tool.call.arguments": "a" * 4000,
            "gen_ai.tool.call.result": tool_output,
        },
    )
    text, truncated = format_evidence(evidence([event]))
    assert truncated and "SEC filing" in text and "IMPORTANT_END" in text
    assert "sha256=" + hashlib.sha256(tool_output.encode()).hexdigest() in text
    assert "chars=4000" in text and len(text) < 6000
    event = TraceEvent(id="m", kind="model", name="model", content={"output.value": "x" * 180000 + "IMPORTANT_END"})
    text, truncated = format_evidence(evidence([event]))
    assert truncated and len(text) <= TRACE_CAP and text.endswith("IMPORTANT_END")
    assert "trace middle truncated" in text and "sha256=" in text


def test_model_tool_call_arguments_are_bounded_and_reported_as_truncated():
    event = TraceEvent(
        id="m",
        kind="model",
        name="model",
        content={
            "gen_ai.output.messages": [
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "call-1", "function": {"name": "SEC", "arguments": "x" * 10000}}],
                }
            ]
        },
    )
    text, truncated = format_evidence(evidence([event]))
    assert truncated and len(text) < 2500 and "call-1" in text and "SEC" in text
    assert "arguments truncated" in text and "sha256=" in text


def test_neutral_grading_uses_identical_weighting_and_verdict_validation_without_inspect_messages(monkeypatch):
    calls = []

    async def fake(model, role, instruction, payload):
        assert "Look up revenue" in payload["trace"]
        assert '"revenue": 100' in payload["trace"]
        assert "4 million" not in payload["trace"]
        assert payload["final_answer"] == "4 million"
        assert all("points" not in item for item in payload["rubric"])
        calls.append((model, role))
        return {
            "final_answer_correct": True,
            "rubric": [
                {"index": 1, "satisfied": True, "explanation": "Correct period"},
                {"index": 2, "satisfied": False, "explanation": "Adjustment not supported"},
            ],
        }

    monkeypatch.setattr(judging, "ask_json", fake)
    result = asyncio.run(
        evaluate_evidence(
            "4 million", get_callback(callback_evidence()), REFERENCE, judge_models=["mock/j1", "mock/j2"]
        )
    )
    assert result["protocol"] == PROTOCOL
    assert result["scores"] == {"rubric": 0.25, "final_answer": 1}
    assert len(calls) == 2 and result["jury_mode"] == "multi-judge-adaptation"
    assert len(result["trace_sha256"]) == 64 and not result["trace_truncated_for_judge"]


@pytest.mark.parametrize(
    "verdict",
    [
        {"final_answer_correct": True, "rubric": []},
        {
            "final_answer_correct": True,
            "rubric": [
                {"index": 1, "satisfied": True, "explanation": "ok"},
                {"index": 1, "satisfied": True, "explanation": "duplicate"},
            ],
        },
        {"final_answer_correct": "true", "rubric": []},
    ],
)
def test_neutral_grading_rejects_the_same_invalid_judge_verdicts(monkeypatch, verdict):
    async def fake(*args, **kwargs):
        return verdict

    monkeypatch.setattr(judging, "ask_json", fake)
    monkeypatch.setattr(judging, "repair_json", fake)
    with pytest.raises(GradingError) as caught:
        asyncio.run(
            evaluate_evidence("answer", get_callback(callback_evidence()), REFERENCE, judge_models=["mock/judge"])
        )
    assert caught.value.stage == "json_schema" and caught.value.repair_attempted is True


@pytest.mark.parametrize("models", [[], [""], ["mock/judge", "mock/judge"]])
def test_neutral_grading_requires_explicit_distinct_judges(models):
    with pytest.raises(GradingError) as caught:
        asyncio.run(evaluate_evidence("answer", get_callback(callback_evidence()), REFERENCE, judge_models=models))
    assert caught.value.stage == "configuration"


@pytest.mark.parametrize("value", ['{"profit":4,"units":"million"}', {"profit": 4}, [4, 5]])
def test_actual_visible_json_answers_are_not_discarded_as_provider_envelopes(value):
    text, _ = format_evidence(
        evidence([TraceEvent(id="m", kind="model", name="actual JSON answer", content={"output.value": value})])
    )
    assert "4" in text


NESTED_PROVIDER_VALUES = [
    {"choices": [{"message": {"role": "assistant", "content": "VISIBLE", "reasoning_content": "PRIVATE"}}]},
    {
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "PRIVATE"}]},
            {"type": "unsupported_provider_block", "opaque": "PRIVATE"},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "VISIBLE"}]},
        ]
    },
    {"result": {"text": "VISIBLE", "reasoning_content": "PRIVATE", "provider_private": "PRIVATE"}},
    [{"type": "output_text", "text": "VISIBLE", "provider_private": "PRIVATE"}],
    [[{"result": {"text": "VISIBLE", "analysis": "PRIVATE"}}, {"type": "analysis", "text": "PRIVATE"}]],
    {"result": {"data": [{"year": 2024, "revenue": 100, "reasoning": "PRIVATE"}], "text": "VISIBLE"}},
]


@pytest.mark.parametrize("value", NESTED_PROVIDER_VALUES)
@pytest.mark.parametrize("key", ["output.value", "everybench.content.output"])
def test_nested_provider_reasoning_is_removed_from_formatted_payload_and_judge(monkeypatch, value, key):
    e = evidence([TraceEvent(id="m", kind="model", name="actual model", content={key: value})])
    text, _ = format_evidence(e)
    assert "VISIBLE" in text and "PRIVATE" not in text
    seen = []

    async def fake(model, role, instruction, payload):
        assert payload["trace"] == text
        assert "PRIVATE" not in payload["trace"]
        seen.append(payload)
        return {
            "final_answer_correct": True,
            "rubric": [{"index": i, "satisfied": True, "explanation": "visible support"} for i in (1, 2)],
        }

    monkeypatch.setattr(judging, "ask_json", fake)
    asyncio.run(evaluate_evidence("answer", e, REFERENCE, judge_models=["mock/judge"]))
    assert len(seen) == 1


def test_nested_financial_json_arrays_are_preserved_without_provider_metadata():
    value = {
        "result": [[{"type": "revenue", "year": 2024, "amount": 100, "missing": None}], [4, 5], [], {}],
        "currency": "USD",
        "output": [1, 2],
    }
    event = TraceEvent(id="m", kind="model", name="financial answer", content={"output.value": value})
    from everybench.evaluation.evidence import visible_content

    assert visible_content(event)["output.value"] == value
    event.content = {"output.value": {"object": "response", "usage": {"private": "UNSUPPORTED_PROVIDER"}}}
    with pytest.raises(TraceAccessError):
        format_evidence(evidence([event]))


@pytest.mark.parametrize("kind", ["scorer", "evaluator", "judge"])
@pytest.mark.parametrize("attrs", [{"gen_ai.operation.name": "chat"}, {"openinference.span.kind": "LLM"}])
def test_explicit_evaluator_classification_survives_conflicting_semantics_directly(kind, attrs):
    events = [
        TraceEvent(id="actual", kind="model", name="actual", content={"output.value": "ACTUAL"}),
        TraceEvent(id="grader", kind=kind, name="grader", attributes=attrs, content={"output.value": "PRIVATE"}),
        TraceEvent(id="child", parent_id="grader", kind="model", name="child", content={"output.value": "PRIVATE"}),
        TraceEvent(id="grandchild", parent_id="child", kind="tool", name="tool", content={"tool.result": "PRIVATE"}),
    ]
    text, _ = format_evidence(evidence(events))
    assert "ACTUAL" in text and "PRIVATE" not in text and "grader" not in text


@pytest.mark.parametrize("on_root", [False, True])
def test_producer_withheld_marker_rejects_direct_grading_even_with_retained_input(on_root, monkeypatch):
    e = callback_evidence()
    event = e.events[0 if on_root else 1]
    event.attributes.update(
        {"everybench.evidence.withheld": True, "everybench.evidence.withheld_keys": ["output.value"]}
    )
    e.events[1].content = {"input.value": "Retained visible input"}

    async def never(*args):
        pytest.fail("Withheld evidence must not reach a judge")

    monkeypatch.setattr(judging, "ask_json", never)
    with pytest.raises(TraceAccessError, match="withheld"):
        format_evidence(e)
    with pytest.raises(GradingError) as caught:
        asyncio.run(evaluate_evidence("answer", e, REFERENCE, judge_models=["mock/judge"]))
    assert caught.value.stage == "evidence"


@pytest.mark.parametrize("key", ["gen_ai.output.messages", "output.value", "everybench.content.output"])
@pytest.mark.parametrize("camel_case", [False, True])
def test_captured_model_tool_calls_keep_visible_action_details_and_caps(key, camel_case):
    call = (
        {
            "id": "call-1",
            "name": "SEC",
            "input": {"ticker": "ACME", "padding": "x" * 10000, "reasoning_content": "PRIVATE"},
        }
        if camel_case
        else {
            "id": "call-1",
            "function": {
                "name": "SEC",
                "arguments": {"ticker": "ACME", "padding": "x" * 10000, "reasoning_content": "PRIVATE"},
            },
        }
    )
    event = TraceEvent(
        id="m",
        kind="model",
        name="model action",
        content={key: [{"role": "assistant", "content": "", "toolCalls" if camel_case else "tool_calls": [call]}]},
    )
    text, cut = format_evidence(evidence([event]))
    assert "call-1" in text and "SEC" in text and "ACME" in text
    assert "PRIVATE" not in text and cut and len(text) < 2500 and "sha256=" in text
