import asyncio
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import select

from everybench.artifacts import read_sample_artifact
from everybench.db import Run, SampleResult, Span, Trace, session_scope
from everybench.evaluation import (
    AgentResult,
    BenchmarkCase,
    CallableAgent,
    CallbackTraceSource,
    GradeResult,
    TraceEvent,
    TraceEvidence,
    evaluate,
    evaluate_sync,
    report_path,
)
from everybench.evaluation.engine import _public


class SyntheticBenchmark:
    name = "synthetic"
    requires_trace = False

    def __init__(self, *, score=1, requires_trace=False, grade_error=False):
        self.score, self.requires_trace, self.grade_error = score, requires_trace, grade_error
        self.graded = []

    def describe(self):
        return {"protocol": "synthetic-v1", "grading": "exact"}

    def cases(self):
        return [
            BenchmarkCase(id=f"original/{n}", input=f"Question {n}", reference={"gold": "PRIVATE GOLD"})
            for n in range(3)
        ]

    async def grade(self, case, result, evidence):
        self.graded.append((case, result, evidence))
        if self.grade_error:
            raise RuntimeError("secret grader failure")
        return GradeResult(score=self.score, components={"accuracy": self.score}, protocol="synthetic-v1")


def complete_evidence(trace_id, case):
    return TraceEvidence(
        trace_id=trace_id,
        state="complete",
        events=[
            TraceEvent(
                id="1" * 16,
                parent_id=case.parent_span_id,
                kind="agent",
                name="Agent",
                attributes={
                    "everybench.run_id": case.run_id,
                    "everybench.sample_id": case.sample_id,
                    "everybench.attempt": case.attempt,
                },
            ),
            TraceEvent(
                id="2" * 16,
                parent_id="1" * 16,
                kind="model",
                name="Real model",
                attributes={"gen_ai.response.model": "actual-model-v2"},
                content={"output.value": "Actual captured model body"},
            ),
            TraceEvent(
                id="3" * 16,
                parent_id="1" * 16,
                kind="tool",
                name="Real tool",
                content={"tool.result": "Actual captured tool body"},
            ),
        ],
    )


def test_callable_selection_identity_original_reference_and_unknown_cost():
    inputs = []
    benchmark = SyntheticBenchmark(score=0)

    async def agent(case):
        inputs.append(case)
        return "completed answer"

    report = evaluate_sync(
        benchmark,
        CallableAgent(agent, name="my agent", version="abc123"),
        allow_paid=True,
        sample_ids=["original/2", "original/0"],
        content_policy="full",
    )
    assert [r.sample_id for r in report.results] == ["original/0", "original/2"]
    assert all(r.status == "scored" and r.score == 0 for r in report.results)
    assert len({c.trace_id for c in inputs}) == len({c.parent_span_id for c in inputs}) == 2
    assert all(
        set(c.model_dump()) == {"input", "run_id", "sample_id", "attempt", "trace_id", "parent_span_id"} for c in inputs
    )
    assert all("PRIVATE" not in c.model_dump_json() for c in inputs)
    assert report.metrics["cost_usd"] is None
    assert report.target["name"] == "my agent" and report.target["version"] == "abc123"
    assert json.loads(report_path(report.run_id).read_text()) == report.model_dump()
    with session_scope() as session:
        run = session.get(Run, report.run_id)
        assert run.model is None and run.target_kind == "external_agent" and run.cost_usd is None
        assert run.n_error == 0 and run.n_correct == 0 and run.samples_done == 2
        assert run.target == report.target
    artifact = read_sample_artifact(report.run_id, "original/0", 1)
    assert artifact["content"]["case"]["reference"] == {"gold": "PRIVATE GOLD"}


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"allow_paid": False},
        {"allow_paid": True, "limit": 0},
        {"allow_paid": True, "sample_ids": ["missing"]},
        {"allow_paid": True, "sample_ids": ["original/0", "original/0"]},
        {"allow_paid": True, "timeout": float("nan")},
        {"allow_paid": True, "content_policy": "bad"},
    ],
)
def test_consent_and_invalid_options_never_invoke(options):
    calls = []
    with pytest.raises(ValueError):
        evaluate_sync(SyntheticBenchmark(), lambda case: calls.append(case) or "answer", **options)
    assert calls == []
    with session_scope() as session:
        assert list(session.scalars(select(Run))) == []


@pytest.mark.parametrize(
    "failure,expected",
    [("agent", "agent_error"), ("timeout", "timeout"), ("grade", "grading_error"), ("grade-timeout", "grading_error")],
)
def test_failures_are_not_incorrect_zero_and_never_retried(failure, expected):
    calls = []
    benchmark = SyntheticBenchmark(grade_error=failure == "grade")
    original_grade = benchmark.grade

    async def grade(*args):
        if failure == "grade-timeout":
            await asyncio.sleep(2)
        return await original_grade(*args)

    benchmark.grade = grade

    async def agent(case):
        calls.append(case)
        if failure == "timeout":
            await asyncio.sleep(2)
        if failure == "agent":
            raise RuntimeError("DO NOT PERSIST THIS SECRET")
        return "answer"

    report = evaluate_sync(benchmark, agent, allow_paid=True, limit=1, timeout=0.02, grading_timeout=0.02)
    result = report.results[0]
    assert result.status == expected and result.score is None and report.status == "failed"
    assert len(calls) == 1 and report.metrics["mean_score"] is None
    assert "SECRET" not in report.model_dump_json() and "secret grader" not in report.model_dump_json()
    with session_scope() as session:
        sample = session.scalar(select(SampleResult).where(SampleResult.run_id == report.run_id))
        assert sample.score is None and sample.outcome == "error"


@pytest.mark.parametrize("state", ["missing", "pending", "withheld", "incomplete", "bad-binding", "access-error"])
def test_trace_required_missing_withheld_timeout_and_untrusted_binding(state):
    benchmark = SyntheticBenchmark(requires_trace=True)

    async def source(trace_id, case):
        if state == "access-error":
            raise RuntimeError("credential value must not persist")
        if state in ("missing", "pending"):
            return TraceEvidence(trace_id=trace_id, state=state)
        evidence = complete_evidence(trace_id, case)
        if state == "bad-binding":
            evidence.events[0].attributes["everybench.run_id"] = "someone-else"
        else:
            evidence.state = state
        return evidence

    report = evaluate_sync(
        benchmark,
        lambda case: "answer",
        trace_source=CallbackTraceSource(source),
        allow_paid=True,
        limit=1,
        trace_timeout=0.02,
    )
    result = report.results[0]
    assert result.status == "trace_missing" and result.score is None and not benchmark.graded
    assert "credential value" not in report.model_dump_json()


def test_response_only_grading_keeps_trace_diagnostic():
    benchmark = SyntheticBenchmark()
    report = evaluate_sync(
        benchmark,
        lambda case: "answer",
        allow_paid=True,
        limit=1,
        trace_source=CallbackTraceSource(lambda tid, case: TraceEvidence(trace_id=tid, state="missing")),
        trace_timeout=0.01,
    )
    assert report.results[0].status == "scored" and report.results[0].trace_state == "missing"


def test_complete_real_evidence_is_separate_from_judge_traffic_and_inspectable():
    benchmark = SyntheticBenchmark(requires_trace=True)
    report = evaluate_sync(
        benchmark,
        lambda case: "answer",
        trace_source=CallbackTraceSource(complete_evidence),
        allow_paid=True,
        limit=1,
        content_policy="full",
    )
    assert report.results[0].status == "scored" and report.results[0].trace_state == "complete"
    with session_scope() as session:
        trace = session.get(Trace, report.results[0].trace_id)
        assert trace.run_id == report.run_id and trace.span_count == 3
        spans = list(session.scalars(select(Span).where(Span.trace_id == trace.trace_id)))
        assert len(spans) == 3 and {s.kind for s in spans} == {"agent", "model", "tool"}
    assert len(benchmark.graded[0][2].events) == 3


@pytest.mark.parametrize("policy", ["metadata", "redacted", "full"])
def test_policy_before_all_report_artifact_and_trace_writes(policy, everybench_home):
    secret = "sk-abcdefghijklmnopqrstuvwx"
    benchmark = SyntheticBenchmark(requires_trace=True)

    def source(tid, case):
        evidence = complete_evidence(tid, case)
        evidence.events[1].content["output.value"] = secret
        return evidence

    report = evaluate_sync(
        benchmark,
        lambda case: "answer " + secret,
        allow_paid=True,
        limit=1,
        trace_source=CallbackTraceSource(source),
        content_policy=policy,
    )
    text = "\n".join(p.read_text() for p in (everybench_home / "files").rglob("*.json"))
    with session_scope() as session:
        sample = session.scalar(select(SampleResult).where(SampleResult.run_id == report.run_id))
        spans = list(session.scalars(select(Span)))
        stored = json.dumps({"answer": sample.answer, "target": sample.target, "spans": [s.content for s in spans]})
    if policy == "full":
        assert secret in text and secret in stored
    else:
        assert secret not in text and secret not in stored
    if policy == "metadata":
        assert report.results[0].answer is None and all(s.content is None for s in spans)
        assert "PRIVATE GOLD" not in text and "PRIVATE GOLD" not in stored


def test_private_target_config_never_persists_even_full_policy(everybench_home):
    class Target:
        def describe(self):
            return {
                "type": "custom",
                "name": "real-agent",
                "version": "v2",
                "api_key": "private-key",
                "headers": {"Authorization": "private-header"},
                "url": "https://example.test/private-path",
            }

        async def invoke(self, case):
            return AgentResult(answer="answer")

    report = evaluate_sync(
        SyntheticBenchmark(),
        Target(),
        allow_paid=True,
        limit=1,
        content_policy="full",
        target_config={
            "strategy": "two-model",
            "api_key": "private-key",
            "nested": {"password": "private-password", "temperature": 0.5, "token_env": "PRIVATE_ENV"},
        },
    )
    assert report.target["config"] == {"strategy": "two-model", "nested": {"temperature": 0.5}}
    text = report.model_dump_json() + "\n".join(p.read_text() for p in (everybench_home / "files").rglob("*.json"))
    with session_scope() as session:
        run = session.get(Run, report.run_id)
        text += json.dumps(run.target) + json.dumps(run.manifest)
    assert "private-" not in text and "PRIVATE_ENV" not in text


def test_cancellation_persists_and_propagates():
    async def scenario():
        invoked = asyncio.Event()

        async def agent(case):
            invoked.set()
            await asyncio.sleep(30)

        task = asyncio.create_task(evaluate(SyntheticBenchmark(), agent, allow_paid=True, limit=1))
        await invoked.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with session_scope() as session:
            run = session.scalar(select(Run))
            assert run.status == "cancelled" and run.samples_done == 1
            persisted = json.loads(report_path(run.id).read_text())
        assert persisted["status"] == "cancelled" and persisted["results"][0]["status"] == "cancelled"

    asyncio.run(scenario())


def test_invalid_components_are_grading_error_not_score():
    benchmark = SyntheticBenchmark()

    async def grade(*args):
        return GradeResult(score=1, components={"invalid": 3}, protocol="synthetic-v1")

    benchmark.grade = grade
    report = evaluate_sync(benchmark, lambda case: "answer", allow_paid=True, limit=1)
    assert report.results[0].status == "grading_error" and report.results[0].score is None


def test_known_usage_preserved_without_claiming_remote_cost():
    report = evaluate_sync(
        SyntheticBenchmark(),
        lambda case: AgentResult(
            answer="answer", usage={"input_tokens": 12, "output_tokens": 7, "api_key": "private usage field"}
        ),
        allow_paid=True,
        limit=1,
        content_policy="metadata",
    )
    assert report.metrics["input_tokens"] == 12 and report.metrics["output_tokens"] == 7
    assert report.metrics["cost_usd"] is None and "private usage" not in report.model_dump_json()
    assert report.results[0].metadata["usage"] == {"input_tokens": 12, "output_tokens": 7}
    with session_scope() as session:
        run = session.get(Run, report.run_id)
        assert run.input_tokens == 12 and run.output_tokens == 7 and run.cost_usd is None


def test_db_cancellation_stops_current_case_without_invoking_another():
    calls = []

    async def agent(case):
        calls.append(case)
        with session_scope() as session:
            session.get(Run, case.run_id).status = "cancelling"
        await asyncio.sleep(30)

    with pytest.raises(asyncio.CancelledError):
        evaluate_sync(SyntheticBenchmark(), agent, allow_paid=True)
    assert len(calls) == 1
    with session_scope() as session:
        assert session.get(Run, calls[0].run_id).status == "cancelled"


def test_generic_sdk_import_does_not_require_inspect(tmp_path):
    code = """
import sys
class NoInspect:
    def find_spec(self, fullname, *args):
        if fullname == "inspect_ai" or fullname.startswith("inspect_ai."):
            raise AssertionError("Inspect imported on generic callable path")
sys.meta_path.insert(0, NoInspect())
from everybench.sdk import BenchmarkCase, GradeResult, evaluate_sync
class Benchmark:
    name = "offline"
    requires_trace = False
    def describe(self): return {"protocol": "test"}
    def cases(self): return [BenchmarkCase(id="original", input="Q")]
    async def grade(self, *args): return GradeResult(score=1, protocol="test")
r = evaluate_sync(Benchmark(), lambda case: "A", allow_paid=True)
assert r.results[0].score == 1
assert not any(m.startswith("inspect_ai") for m in sys.modules)
"""
    env = {**os.environ, "EVERYBENCH_HOME": str(tmp_path / "subprocess")}
    subprocess.run([sys.executable, "-c", code], check=True, env=env, capture_output=True, text=True)


@pytest.mark.parametrize("missing_raw_span", [False, True])
def test_sealed_callback_scorer_subtree_survives_engine_revalidation_but_raw_mismatch_fails(missing_raw_span):
    benchmark = SyntheticBenchmark(requires_trace=True)

    async def source(trace_id, case):
        e = complete_evidence(trace_id, case)
        e.events.extend(
            [
                TraceEvent(
                    id="grader",
                    parent_id=e.events[0].id,
                    kind="scorer",
                    name="grader",
                    attributes={"gen_ai.operation.name": "chat"},
                    content={"output.value": "PRIVATE"},
                ),
                TraceEvent(
                    id="judge", parent_id="grader", kind="model", name="judge", content={"output.value": "PRIVATE"}
                ),
            ]
        )
        e.metadata.update({"expected_spans": 5, "sealed_at": "2026-01-01T12:00:00+00:00"})
        if missing_raw_span:
            e.events.pop()
        return e

    report = evaluate_sync(
        benchmark,
        lambda case: "answer",
        trace_source=CallbackTraceSource(source),
        allow_paid=True,
        limit=1,
        content_policy="full",
    )
    result = report.results[0]
    if missing_raw_span:
        assert result.status == "trace_missing" and result.score is None and not benchmark.graded
    else:
        assert result.status == "scored" and result.trace_state == "complete"
        graded_evidence = benchmark.graded[0][2]
        assert len(graded_evidence.events) == 3 and graded_evidence.metadata["expected_spans"] == 5
        assert graded_evidence.metadata["completeness_basis"] == "producer_seal"
        assert "PRIVATE" not in graded_evidence.model_dump_json()


@pytest.mark.parametrize(
    "limit_key", ["max_tokens", "token_budget", "token_limit", "max_output_tokens", "max_completion_tokens"]
)
def test_public_numeric_token_limits_persist_and_distinguish_target_and_grading_identity(limit_key):
    reports = []
    for limit in (512, 8192):
        benchmark = SyntheticBenchmark()
        benchmark.describe = lambda limit=limit: {
            "protocol": "synthetic-v1",
            "grading": {limit_key: limit, "token_env": "PRIVATE_GRADING_ENV", "access_token": "private-grading-token"},
        }
        report = evaluate_sync(
            benchmark,
            lambda case: "answer",
            allow_paid=True,
            limit=1,
            content_policy="full",
            target_config={
                limit_key: limit,
                "nested": {"max_tokens": 256, "token_limit": "private-not-a-number", "token_budget": True},
                "access_token": "private-token",
                "accessToken": "private-camel-token",
                "myAPIToken": "private-acronym-token",
                "tokenEnv": "PRIVATE_CAMEL_ENV",
                "token_env": "PRIVATE_TARGET_ENV",
                "tokenizer": "public-tokenizer",
                "auth": "private-auth",
                "request_headers": {"Authorization": "private-header"},
                "client_secret": "private-secret",
            },
        )
        assert report.target["config"] == {
            limit_key: limit,
            "nested": {"max_tokens": 256},
            "tokenizer": "public-tokenizer",
        }
        with session_scope() as session:
            run = session.get(Run, report.run_id)
            assert run.target == report.target
            assert run.manifest["evaluation_contract"]["description"]["grading"] == {limit_key: limit}
            saved = json.dumps(run.manifest) + json.dumps(run.target)
        assert "private-" not in saved and "PRIVATE_" not in saved
        reports.append(report)
    assert reports[0].target["config_sha256"] != reports[1].target["config_sha256"]
    with session_scope() as session:
        assert session.get(Run, reports[0].run_id).variant_key != session.get(Run, reports[1].run_id).variant_key


def test_fractional_mean_score_persists_during_evaluation_without_counting_failures_as_zero():
    calls = []

    async def agent(case):
        if calls:
            with session_scope() as session:
                run = session.get(Run, case.run_id)
                assert run.metrics == {"mean_score": 0.5, "scored": 1, "errors": len(calls) - 1}
                assert run.status == "running"
        calls.append(case)
        if len(calls) == 2:
            raise RuntimeError("synthetic failure")
        return "answer"

    report = evaluate_sync(SyntheticBenchmark(score=0.5), agent, allow_paid=True)
    assert [r.score for r in report.results] == [0.5, None, 0.5]
    assert report.metrics["mean_score"] == 0.5 and report.metrics["scored"] == 2 and report.metrics["errors"] == 1
    with session_scope() as session:
        run = session.get(Run, report.run_id)
        assert run.metrics == report.metrics and run.n_correct == 0 and run.n_error == 1


@pytest.mark.parametrize(
    "key",
    [
        "max_tokens",
        "maxTokens",
        "MAX.TOKENS",
        "max-tokens",
        "max_output_tokens",
        "maxOutputTokens",
        "max_completion_tokens",
        "maxCompletionTokens",
        "token_budget",
        "tokenBudget",
        "token_limit",
        "TOKEN-LIMIT",
    ],
)
@pytest.mark.parametrize(
    "value", [True, False, float("nan"), float("inf"), -float("inf"), "opaqueSyntheticLimit91735", -1]
)
def test_public_token_limit_exceptions_reject_non_numeric_or_nonfinite_values(key, value):
    assert _public({key: value}) == {}


@pytest.mark.parametrize(
    "key",
    [
        "max_tokens",
        "maxTokens",
        "MAX.TOKENS",
        "max-tokens",
        "max_output_tokens",
        "maxOutputTokens",
        "max_completion_tokens",
        "maxCompletionTokens",
        "token_budget",
        "tokenBudget",
        "token_limit",
        "TOKEN-LIMIT",
    ],
)
@pytest.mark.parametrize("value", [0, 512, 8192.0])
def test_public_token_limit_exceptions_normalize_key_spelling_before_validation(key, value):
    assert _public({key: value}) == {key: value}


def test_public_tokenizer_exceptions_are_explicit_and_still_filter_nested_credentials():
    config = {
        "tokenizer": {"name": "fixture-tokenizer", "tokenEnv": "opaqueSyntheticTokenizer91735"},
        "tokenizerName": "fixture-tokenizer",
        "TOKENIZER.REVISION": "v1",
        "tokenizerCredential": "opaqueSyntheticTokenizer24860",
        "unknown_token_configuration": "opaqueSyntheticUnknownToken50319",
    }
    assert _public(config) == {
        "tokenizer": {"name": "fixture-tokenizer"},
        "tokenizerName": "fixture-tokenizer",
        "TOKENIZER.REVISION": "v1",
    }


@pytest.mark.parametrize("status", ["failed", "succeeded", "cancelled", "budget_exceeded", "queued", "missing"])
def test_bounded_checks_ownership_before_creating_awaitable(status):
    from everybench.evaluation.engine import _bounded, _EvaluationStopped

    run_id = "run_stopped"
    if status != "missing":
        with session_scope() as session:
            session.add(Run(id=run_id, benchmark="synthetic", variant_key="test", status=status))
    calls = []

    def start():
        calls.append("started")
        return asyncio.get_running_loop().create_future()

    async def scenario():
        with pytest.raises(asyncio.CancelledError if status == "cancelled" else _EvaluationStopped):
            await _bounded(start, 1, run_id)

    asyncio.run(scenario())
    assert not calls


@pytest.mark.parametrize("completed", [False, True])
def test_bounded_checks_state_during_wait_and_after_already_completed_future(completed):
    from everybench.evaluation.engine import _bounded, _EvaluationStopped

    run_id = "run_bounded"
    with session_scope() as session:
        session.add(Run(id=run_id, benchmark="synthetic", variant_key="test", status="running"))

    async def scenario():
        future = asyncio.get_running_loop().create_future()

        def stop():
            with session_scope() as session:
                session.get(Run, run_id).status = "failed"

        def start():
            if completed:
                future.set_result("must not proceed")
                stop()
            else:
                asyncio.get_running_loop().call_later(0.01, stop)
            return future

        with pytest.raises(_EvaluationStopped):
            await asyncio.wait_for(_bounded(start, 5, run_id), timeout=1)
        assert future.done()
        assert completed or future.cancelled()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["agent", "trace", "grading"])
def test_immediate_terminal_stop_is_not_swallowed_or_scored(phase):
    calls = []
    benchmark = SyntheticBenchmark()

    def stop(run_id):
        with session_scope() as session:
            run = session.get(Run, run_id)
            run.status, run.error = "failed", "Original stale failure."

    async def agent(case):
        calls.append("agent")
        if phase == "agent":
            stop(case.run_id)
        return "answer"

    async def trace(tid, case):
        calls.append("trace")
        if phase == "trace":
            stop(case.run_id)
        return complete_evidence(tid, case)

    async def grade(case, result, evidence):
        calls.append("grading")
        if phase == "grading":
            stop(evidence.run_id)
        return GradeResult(score=1, protocol="synthetic-v1")

    benchmark.grade = grade
    report = evaluate_sync(
        benchmark, agent, trace_source=CallbackTraceSource(trace), allow_paid=True, content_policy="metadata"
    )
    assert calls == ["agent", "trace", "grading"][: ["agent", "trace", "grading"].index(phase) + 1]
    assert report.status == "failed" and len(report.results) == 1
    assert report.results[0].status == "unscored" and report.results[0].score is None
    assert report.metrics["mean_score"] is None
    with session_scope() as session:
        run = session.get(Run, report.run_id)
        assert run.status == "failed" and run.error == "Original stale failure."
