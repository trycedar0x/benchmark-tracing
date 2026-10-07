from sqlalchemy import select

from everyeval.catalog import get_benchmark
from everyeval.compare import compare_runs
from everyeval.db import SampleResult, session_scope
from everyeval.execution import execute_run
from everyeval.service import create_runs
from everyeval.trace_diff import diff_traces


def _runs():
    a, b = create_runs(get_benchmark("toy-tools"), ["mock/strong", "mock/flaky"], limit=15)
    return execute_run(a.id), execute_run(b.id)


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
