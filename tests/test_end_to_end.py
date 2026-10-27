"""Read-only historical model-run compatibility; no retired solver execution."""

from historical import historical_run
from sqlalchemy import select

from everybench.compare import compare_runs
from everybench.db import SampleResult, Span, session_scope


def run_one(benchmark, model, **kwargs):
    return historical_run(benchmark=benchmark, model=model, **kwargs)


def test_paired_comparison_between_models():
    a = run_one("toy-arith", "mock/strong")
    b = run_one("toy-arith", "mock/weak")
    result = compare_runs(a.id, b.id)
    assert result.compatibility.comparable
    assert result.n_paired == 40
    assert result.delta < 0
    assert result.delta_ci95[0] <= result.delta <= result.delta_ci95[1]
    assert result.mcnemar_p is not None and result.mcnemar_p < 0.05
    assert result.discordant["regressions"] > result.discordant["improvements"]
    regressions = [r for r in result.rows if r.change == "regression"]
    assert regressions and regressions[0].a_trace_id and regressions[0].b_trace_id


def test_errors_are_excluded_from_scores():
    a = run_one("toy-tools", "mock/strong")
    b = run_one("toy-tools", "mock/flaky")
    assert b.n_error > 0
    result = compare_runs(a.id, b.id)
    assert result.errors["b_only"] == b.n_error
    assert result.n_scored_pairs == result.n_paired - b.n_error


def test_different_variants_are_blocked():
    a = run_one("toy-arith", "mock/strong", limit=3)
    b = run_one("toy-tools", "mock/strong", limit=3)
    result = compare_runs(a.id, b.id)
    assert not result.compatibility.comparable
    assert result.delta is None


def test_model_revision_drift_blocks_strict_comparison():
    a = run_one("toy-arith", "mock/strong")
    b = run_one("toy-arith", "mock/drifty")
    assert len(b.resolved_models) == 2
    result = compare_runs(a.id, b.id)
    assert not result.compatibility.comparable
    assert any("more than one model revision" in m for m in result.compatibility.blocking)
    assert compare_runs(a.id, b.id, force=True).delta is not None


def test_metadata_policy_withholds_content():
    run = run_one("toy-arith", "mock/strong", limit=2, content_policy="metadata")
    with session_scope() as session:
        samples = session.scalars(select(SampleResult).where(SampleResult.run_id == run.id)).all()
        spans = session.scalars(select(Span).where(Span.run_id == run.id)).all()
    assert all(s.input is None and s.output is None and s.answer is None for s in samples)
    assert all(s.content is None for s in spans)
    assert any(s.content_state == "withheld" for s in spans)


def test_historical_harbor_code_and_task_checksum_changes_block_comparison():
    from everybench.compare import check_compatibility

    a = historical_run()
    b = historical_run()
    keys = {("sample-1", 1)}
    a.manifest = {"harbor": {"benchmark_revisions": {"task": "revision-a"}, "task_checksums": {"task": "hash-a"}}}
    b.manifest = {"harbor": {"benchmark_revisions": {"task": "revision-b"}, "task_checksums": {"task": "hash-b"}}}
    result = check_compatibility(a, b, keys, keys)
    assert not result.comparable
    assert any("Benchmark code differs" in reason for reason in result.blocking)
    assert any("Task contents changed" in reason for reason in result.blocking)
