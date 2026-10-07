"""End-to-end runs with the offline btmock models (no network, no API keys)."""

import threading
import time

import pytest
from sqlalchemy import select

from benchtrace.catalog import get_benchmark
from benchtrace.compare import compare_runs
from benchtrace.db import Run, SampleResult, Span, session_scope
from benchtrace.execution import execute_run, request_cancel
from benchtrace.service import PlanError, approve_quote, create_quote, create_runs, runs_from_quote


def run_one(benchmark: str, model: str, **kwargs) -> Run:
    [run] = create_runs(get_benchmark(benchmark), [model], **kwargs)
    return execute_run(run.id)


def test_run_records_results_metrics_and_traces():
    run = run_one("toy-arith", "btmock/strong", limit=10)
    assert run.status == "succeeded"
    assert run.samples_done == 10 and run.samples_total == 10
    assert run.metrics["match"]["accuracy"] == pytest.approx(run.n_correct / 10)
    assert run.resolved_models == ["btmock-strong-2026-01"]
    assert run.cost_usd and run.cost_usd > 0
    assert run.manifest["versions"]["inspect_ai"]
    with session_scope() as session:
        sample = session.scalars(select(SampleResult).where(SampleResult.run_id == run.id)).first()
        spans = session.scalars(select(Span).where(Span.trace_id == sample.trace_id)).all()
    kinds = {s.kind for s in spans}
    assert {"agent", "model", "scorer"} <= kinds
    model_span = next(s for s in spans if s.kind == "model")
    assert model_span.content["input"][0]["text"].startswith("What is")
    assert model_span.attributes["gen_ai.usage.input_tokens"] > 0


def test_tool_calls_become_tool_spans():
    run = run_one("toy-tools", "btmock/perfect", limit=3)
    assert run.status == "succeeded" and run.n_correct == 3
    with session_scope() as session:
        tools = session.scalars(
            select(Span).where(Span.run_id == run.id, Span.kind == "tool", Span.name == "calculator")
        ).all()
    assert len(tools) == 3 and all(s.content["result"] for s in tools)


def test_paired_comparison_between_models():
    a = run_one("toy-arith", "btmock/strong")
    b = run_one("toy-arith", "btmock/weak")
    result = compare_runs(a.id, b.id)
    assert result.compatibility.comparable
    assert result.n_paired == 40
    assert result.delta < 0
    assert result.delta_ci95[0] < result.delta < result.delta_ci95[1]
    assert result.mcnemar_p is not None and result.mcnemar_p < 0.05
    assert result.discordant["regressions"] > result.discordant["improvements"]
    regressions = [r for r in result.rows if r.change == "regression"]
    assert regressions and regressions[0].a_trace_id and regressions[0].b_trace_id


def test_errors_are_excluded_from_scores():
    a = run_one("toy-tools", "btmock/strong")
    b = run_one("toy-tools", "btmock/flaky")
    assert b.n_error > 0
    result = compare_runs(a.id, b.id)
    assert result.errors["b_only"] == b.n_error
    assert result.n_scored_pairs == result.n_paired - b.n_error


def test_different_variants_are_blocked():
    a = run_one("toy-arith", "btmock/strong", limit=3)
    b = run_one("toy-tools", "btmock/strong", limit=3)
    result = compare_runs(a.id, b.id)
    assert not result.compatibility.comparable
    assert result.delta is None


def test_model_revision_drift_blocks_strict_comparison():
    a = run_one("toy-arith", "btmock/strong")
    b = run_one("toy-arith", "btmock/drifty")
    assert len(b.resolved_models) == 2
    result = compare_runs(a.id, b.id)
    assert not result.compatibility.comparable
    assert any("more than one model revision" in m for m in result.compatibility.blocking)
    assert compare_runs(a.id, b.id, force=True).delta is not None


def test_metadata_policy_withholds_content():
    run = run_one("toy-arith", "btmock/strong", limit=2, content_policy="metadata")
    with session_scope() as session:
        samples = session.scalars(select(SampleResult).where(SampleResult.run_id == run.id)).all()
        spans = session.scalars(select(Span).where(Span.run_id == run.id)).all()
    assert all(s.input is None and s.output is None and s.answer is None for s in samples)
    assert all(s.content is None for s in spans)
    assert any(s.content_state == "withheld" for s in spans)


def test_budget_cap_stops_run():
    run = run_one("toy-tools", "btmock/strong", budget_usd=0.001)
    assert run.status == "budget_exceeded"
    assert run.samples_done < 30
    assert "Budget cap" in run.error


def test_budget_requires_known_price():
    with pytest.raises(PlanError, match="No price"):
        create_runs(get_benchmark("toy-arith"), ["someprovider/unpriced"], budget_usd=1.0)


def test_cancel_running_run():
    [run] = create_runs(get_benchmark("toy-tools"), ["btmock/strong"], epochs=50)
    holder = {}
    thread = threading.Thread(target=lambda: holder.update(run=execute_run(run.id)))
    thread.start()
    deadline = time.time() + 60
    while time.time() < deadline:
        with session_scope() as session:
            current = session.get(Run, run.id)
            if current.status == "running" and current.samples_done > 0:
                break
        time.sleep(0.2)
    request_cancel(run.id)
    thread.join(timeout=90)
    assert holder["run"].status == "cancelled"
    assert holder["run"].samples_done < 1500


def test_quote_approval_flow():
    quote = create_quote(get_benchmark("toy-arith"), ["btmock/strong", "btmock/weak"], limit=20, sample_size=3)
    est = quote.estimate["btmock/strong"]
    assert est["samples_measured"] == 3
    assert est["cost_usd_range"][0] <= est["cost_usd"] <= est["cost_usd_range"][1]
    with pytest.raises(PlanError, match="approve"):
        runs_from_quote(quote.id)
    approve_quote(quote.id, cap_usd=1.0, approved_by="test")
    runs = runs_from_quote(quote.id)
    assert [r.budget_usd for r in runs] == [0.5, 0.5]
    assert all(r.limit == 20 and r.quote_id == quote.id for r in runs)
