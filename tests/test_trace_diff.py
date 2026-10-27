from historical import historical_run
from sqlalchemy import select

from everybench.compare import compare_runs
from everybench.db import SampleResult, session_scope
from everybench.trace_diff import diff_traces


def _runs():
    return historical_run(model="mock/strong", limit=15), historical_run(model="mock/flaky", limit=15)


def test_regression_diverges_at_final_model_answer():
    a, b = _runs()
    rows = [r for r in compare_runs(a.id, b.id).rows if r.change == "regression"]
    assert rows
    with session_scope() as session:
        result = diff_traces(session, rows[0].a_trace_id, rows[0].b_trace_id)
    kinds = [(p.op, (p.a or p.b).kind) for p in result.pairs]
    assert kinds[:2] == [("same", "model"), ("same", "tool")]  # same tool call, same result
    first = result.pairs[result.first_divergence]
    assert first.op == "changed" and first.a.kind == "model" and "output" in first.differences
    assert "Diverged at step 3" in result.summary


def test_identical_traces_have_no_divergence():
    a, _ = _runs()
    with session_scope() as session:
        trace = session.scalars(select(SampleResult.trace_id).where(SampleResult.run_id == a.id)).first()
        result = diff_traces(session, trace, trace)
    assert result.first_divergence is None
    assert all(p.op == "same" for p in result.pairs)


def test_error_trace_shows_missing_steps():
    a, b = _runs()
    rows = [r for r in compare_runs(a.id, b.id).rows if r.change == "error"]
    assert rows
    with session_scope() as session:
        result = diff_traces(session, rows[0].a_trace_id, rows[0].b_trace_id)
    ops = {p.op for p in result.pairs}
    assert "only_b" in ops and any((p.b and p.b.kind == "error") for p in result.pairs)


def test_sdk_and_otlp_content_keys_feed_summaries_and_previews():
    from everybench.cli import _span_label
    from everybench.db import Span
    from everybench.trace_diff import _output, _summary

    sdk_model = Span(
        kind="model",
        name="chat",
        content={
            "everybench.content.output": "The answer is 4",
            "everybench.content.tool_calls": '[{"function": "calc", "arguments": {"expression": "2+2"}}]',
        },
    )
    sdk_tool = Span(
        kind="tool",
        name="calc",
        content={"everybench.content.arguments": '{"expression": "2+2"}', "everybench.content.result": "4"},
    )
    otlp_model = Span(kind="model", name="llm", content={"output.value": "Paris"})
    otlp_tool = Span(kind="tool", name="search", content={"input.value": "capital of France", "output.value": "Paris"})

    assert _summary(sdk_model).startswith("calls calc(")
    assert _output(sdk_model)["output"] == "The answer is 4"
    assert _summary(sdk_tool) == '{"expression": "2+2"} → 4'
    assert _output(otlp_tool) == {"arguments": "capital of France", "result": "Paris", "error": None}
    assert _summary(otlp_model) == "Paris"
    assert "“The answer is 4”" in _span_label(sdk_model)
    assert "“4”" in _span_label(sdk_tool)
    # Traces recorded before the rename use the benchtrace.content. prefix.
    old_sdk_model = Span(kind="model", name="chat", content={"benchtrace.content.output": "The answer is 4"})
    assert _output(old_sdk_model)["output"] == "The answer is 4"
    assert "“Paris”" in _span_label(otlp_model)
