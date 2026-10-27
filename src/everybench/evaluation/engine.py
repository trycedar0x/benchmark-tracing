"""Sequential, consent-gated evaluation of user-owned agents; never a solver."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import secrets
import tempfile
import time
from collections.abc import Awaitable
from datetime import datetime
from pathlib import Path
from typing import Any

from opentelemetry import context as otel_context
from sqlalchemy import func, select

from everybench.artifacts import write_grading_diagnostic, write_sample_artifact
from everybench.config import settings
from everybench.db import Run, SampleResult, Span, Trace, new_id, now, session_scope
from everybench.evaluation.agents import CallableAgent
from everybench.evaluation.diagnostics import bounded_diagnostic
from everybench.evaluation.traces import _validate_evidence
from everybench.evaluation.types import (
    AgentCallable,
    AgentInput,
    AgentResult,
    AgentTarget,
    Benchmark,
    BenchmarkCase,
    CaseResult,
    EvaluationReport,
    GradeResult,
    TraceEvidence,
    TraceSource,
)
from everybench.redact import POLICIES, SENSITIVE_KEYS, apply_policy, redact_text
from everybench.scoring import normalized_score

_PUBLIC_TARGET_KEYS = {
    "type",
    "name",
    "identity",
    "version",
    "origin",
    "endpoint_sha256",
    "timeout",
    "max_bytes",
    "authenticated",
    "protocol",
    "retries",
}


_PUBLIC_TOKEN_LIMITS = {"max_tokens", "max_output_tokens", "max_completion_tokens", "token_budget", "token_limit"}
_PRIVATE_CONFIG_KEYS = re.compile(r"(^|[_.-])(tokens?|auth|authentication|headers?|cookies?)([_.-]|$)", re.I)


def _public_key(key: str, value: Any) -> bool:
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized).lower()
    normalized = re.sub(r"[_.-]+", "_", normalized)
    if normalized in _PUBLIC_TOKEN_LIMITS:
        return type(value) is int and value >= 0 or type(value) is float and math.isfinite(value) and value >= 0
    if normalized in ("tokenizer", "tokenizer_name", "tokenizer_revision"):
        return True
    return (
        "token" not in key.lower()
        and "token" not in normalized
        and not SENSITIVE_KEYS.search(normalized)
        and not _PRIVATE_CONFIG_KEYS.search(normalized)
        and not any(part in normalized for part in ("credential", "password", "secret", "authorization"))
        and normalized not in ("url", "endpoint", "base_url")
    )


def _public(value: Any) -> Any:
    """Explicit public config only; credentials are discarded even under full policy."""
    if isinstance(value, dict):
        return {k: _public(v) for k, v in value.items() if isinstance(k, str) and _public_key(k, v)}
    if isinstance(value, list | tuple):
        return [_public(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, bool | int) or isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("Public evaluation configuration must contain finite JSON values.")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def report_path(run_id: str) -> Path:
    if not run_id.startswith("run_") or not run_id[4:].isalnum():
        raise ValueError("Invalid run ID.")
    return settings().files_dir / "evaluations" / run_id / "report.json"


def _write_report(report: EvaluationReport) -> None:
    path = report_path(report.run_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".report-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(report.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _seconds(value: float, name: str, *, zero: bool = False) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0 or not zero and value == 0:
        raise ValueError(f"{name} must be finite and {'nonnegative' if zero else 'positive'}.")
    return value


async def _bounded[T](awaitable: Awaitable[T], timeout: float, run_id: str) -> T:
    task = asyncio.ensure_future(awaitable)
    try:
        async with asyncio.timeout(timeout):
            while not task.done():
                await asyncio.wait({task}, timeout=min(0.1, timeout))
                with session_scope() as session:
                    if session.get(Run, run_id).status == "cancelling":
                        raise asyncio.CancelledError
            return task.result()
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def _trace(source: TraceSource | None, case: AgentInput, timeout: float) -> TraceEvidence:
    if source is None:
        return TraceEvidence(trace_id=case.trace_id, state="missing", source="none")
    deadline = time.monotonic() + timeout
    evidence = TraceEvidence(trace_id=case.trace_id, state="missing")
    try:
        async with asyncio.timeout(timeout):
            while True:
                evidence = _validate_evidence(
                    await source.get_trace(case.trace_id, case=case), case, trusted_callback=True
                )
                if evidence.state in ("complete", "withheld") or time.monotonic() >= deadline:
                    return evidence
                await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
    except TimeoutError:
        return evidence


def _filtered_result(result: CaseResult, policy: str) -> CaseResult:
    content, _ = apply_policy({"answer": result.answer, "explanation": result.explanation}, policy)
    return result.model_copy(update=content or {"answer": None, "explanation": None})


def _persist_trace(evidence: TraceEvidence | None, case: AgentInput, policy: str, workspace_id: str | None) -> None:
    if evidence is None or not evidence.events:
        return
    with session_scope() as session:
        existing = session.get(Trace, evidence.trace_id)
        if existing is not None:
            # A local producer owns its trace. Never overwrite its content, policy or workspace.
            if (
                existing.workspace_id != workspace_id
                or existing.run_id not in (None, case.run_id)
                or existing.sample_id not in (None, case.sample_id)
            ):
                raise ValueError("Local trace ownership conflicts with this evaluation.")
            # SDK/OTLP headers can be initially unassociated. Root binding was
            # validated independently; attach the existing trace without rewriting
            # its producer-owned content, seal or retention policy.
            existing.run_id, existing.sample_id = case.run_id, case.sample_id
            for span in session.scalars(select(Span).where(Span.trace_id == case.trace_id)):
                if span.run_id not in (None, case.run_id):
                    raise ValueError("Local span ownership conflicts with this evaluation.")
                span.run_id = case.run_id
            return
        session.add(
            Trace(
                trace_id=evidence.trace_id,
                workspace_id=workspace_id,
                run_id=case.run_id,
                sample_id=case.sample_id,
                name="External agent",
                source="external_agent",
                span_count=len(evidence.events),
                state="closed" if evidence.state == "complete" else "open",
                attributes={"evaluation_trace_state": evidence.state},
            )
        )
        for event in evidence.events:
            content, state = apply_policy(event.content, policy)
            # Event attributes may contain content. Treat all non-structural values as content too.
            attrs, _ = apply_policy(_public(event.attributes), policy)
            session.add(
                Span(
                    trace_id=evidence.trace_id,
                    span_id=event.id,
                    parent_id=event.parent_id,
                    workspace_id=workspace_id,
                    run_id=case.run_id,
                    name=redact_text(event.name),
                    kind=event.kind,
                    start_time=datetime.fromisoformat(event.start_time) if event.start_time else None,
                    end_time=datetime.fromisoformat(event.end_time) if event.end_time else None,
                    status=event.status,
                    attributes=attrs or {},
                    content=content,
                    content_state=state,
                    source="external_agent",
                )
            )


def _save_case(
    case: BenchmarkCase,
    invocation: AgentInput,
    result: CaseResult,
    evidence: TraceEvidence | None,
    grade: GradeResult | None,
    policy: str,
    workspace_id: str | None,
) -> CaseResult:
    _persist_trace(evidence, invocation, policy, workspace_id)
    write_sample_artifact(
        invocation.run_id,
        case.id,
        1,
        {
            "case": case.model_dump(),
            "result": result.model_dump(),
            "trace": evidence.model_dump() if evidence else None,
            "grade": grade.model_dump() if grade else None,
        },
        policy,
    )
    stored = _filtered_result(result, policy)
    content, _ = apply_policy({"input": case.input, "reference": case.reference}, policy)
    score = stored.score
    outcome = (
        ("correct" if score == 1 else "incorrect" if score == 0 else "partial")
        if score is not None
        else "cancelled"
        if result.status == "cancelled"
        else "error"
    )
    with session_scope() as session:
        session.add(
            SampleResult(
                run_id=invocation.run_id,
                sample_id=case.id,
                epoch=1,
                outcome=outcome,
                score=score,
                scorer=grade.protocol if grade else None,
                score_raw=json.dumps(stored.components),
                answer=stored.answer,
                output=stored.answer,
                explanation=stored.explanation,
                input=content["input"] if content else None,
                target=json.dumps(content["reference"]) if content else None,
                error=stored.error,
                input_tokens=result.metadata.get("usage", {}).get("input_tokens", 0),
                output_tokens=result.metadata.get("usage", {}).get("output_tokens", 0),
                total_time=stored.duration_seconds,
                trace_id=stored.trace_id,
            )
        )
        run = session.get(Run, invocation.run_id)
        run.samples_done += 1
        run.n_error += int(score is None)
        run.n_correct += int(score == 1)
        session.flush()
        mean_score = session.scalar(
            select(func.avg(SampleResult.score)).where(SampleResult.run_id == invocation.run_id)
        )
        run.metrics = {"mean_score": mean_score, "scored": run.samples_done - run.n_error, "errors": run.n_error}
        run.input_tokens += result.metadata.get("usage", {}).get("input_tokens", 0)
        run.output_tokens += result.metadata.get("usage", {}).get("output_tokens", 0)
        if evidence is not None:
            identities = [
                event.attributes[key]
                for event in evidence.events
                if event.kind == "model"
                for key in ("gen_ai.response.model", "gen_ai.request.model", "llm.model_name")
                if isinstance(event.attributes.get(key), str)
            ]
            run.resolved_models = sorted(set(run.resolved_models or []) | {redact_text(m) for m in identities})
    return stored


def _grading_error_text(diagnostic: dict[str, Any]) -> str:
    if diagnostic["stage"] == "unspecified":
        return diagnostic["message"]
    return f"{diagnostic['stage']}: {diagnostic['message']}"


def _apply_grading_failure(
    result: CaseResult,
    exc: BaseException,
    run_id: str,
    sample_id: str,
    policy: str,
) -> None:
    """Persist a bounded diagnostic. A grading failure stays unscored."""
    diagnostic = bounded_diagnostic(exc)
    result.status = "grading_error"
    result.score = None
    result.components = {}
    result.error = _grading_error_text(diagnostic)
    result.metadata["grading_diagnostic"] = {
        "stage": diagnostic["stage"],
        "exception": diagnostic["exception"],
        "message": diagnostic["message"],
        "repair_attempted": diagnostic["repair_attempted"],
    }
    excerpt = getattr(exc, "invalid_output", None)
    if diagnostic["stage"] in {"json_parse", "json_schema"} and isinstance(excerpt, str) and excerpt:
        try:
            write_grading_diagnostic(run_id, sample_id, 1, diagnostic, excerpt, policy)
        except Exception:
            # The case result already carries the bounded diagnostic. An excerpt write must not drop it.
            pass


def _finish(report: EvaluationReport) -> EvaluationReport:
    scores = [result.score for result in report.results if result.score is not None]
    report.metrics = {
        "mean_score": sum(scores) / len(scores) if scores else None,
        "scored": len(scores),
        "errors": sum(r.score is None for r in report.results),
        "cost_usd": None,
        "cost_scope": "Remote agent and evaluator costs are not known or capped.",
    }
    for key in ("input_tokens", "output_tokens"):
        counts = [result.metadata.get("usage", {}).get(key) for result in report.results]
        report.metrics[key] = sum(counts) if counts and all(v is not None for v in counts) else None
    _write_report(report)
    with session_scope() as session:
        run = session.get(Run, report.run_id)
        run.status, run.finished_at, run.metrics = report.status, now(), report.metrics
        run.log_path = str(report_path(report.run_id))
    return report


async def evaluate(
    benchmark: Benchmark | str,
    agent: AgentTarget | AgentCallable,
    *,
    trace_source: TraceSource | None = None,
    allow_paid: bool = False,
    sample_ids: list[str] | None = None,
    limit: int | None = None,
    timeout: float = 40,
    trace_timeout: float = 10,
    grading_timeout: float = 180,
    content_policy: str = "redacted",
    workspace_id: str | None = None,
    target_config: dict[str, Any] | None = None,
    judge_models: list[str] | None = None,
    extractor_model: str | None = None,
    data_path: str | Path | None = None,
    tolerance: float = 0.01,
) -> EvaluationReport:
    """Evaluate once per selected original case, with no implicit retries.

    ``allow_paid=True`` consents to target and evaluator invocations, not a billing cap.
    Config is declared public identity, not secrets; fingerprints are not revision proofs.
    Cancelling this coroutine persists a cancelled report then propagates cancellation.
    Synchronous callable threads cannot be forcibly stopped by Python.
    """
    if allow_paid is not True:
        raise ValueError("Invocation requires explicit consent: allow_paid=True (CLI: --yes).")
    if content_policy not in POLICIES:
        raise ValueError("Invalid content policy.")
    timeout = _seconds(timeout, "timeout")
    trace_timeout = _seconds(trace_timeout, "trace_timeout", zero=True)
    grading_timeout = _seconds(grading_timeout, "grading_timeout")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer.")
    from everybench.evaluation.benchmarks import FinanceBenchmark

    if isinstance(benchmark, str):
        benchmark = FinanceBenchmark(
            benchmark,
            judge_models=judge_models or [],
            extractor_model=extractor_model,
            data_path=data_path,
            tolerance=tolerance,
        )
    elif judge_models is not None or extractor_model is not None or data_path is not None or tolerance != 0.01:
        raise ValueError("Configure custom benchmark grading on the benchmark implementation itself.")
    if benchmark.requires_trace and trace_source is None:
        raise ValueError("This benchmark requires an independent trace source.")
    description = _public(benchmark.describe())
    cases = [BenchmarkCase.model_validate(c).model_copy(deep=True) for c in benchmark.cases()]
    ids = [c.id for c in cases]
    if not cases or any(not c.id.strip() or not c.input.strip() for c in cases) or len(set(ids)) != len(ids):
        raise ValueError("Benchmark requires nonempty unique original IDs and inputs.")
    if sample_ids is not None:
        if not sample_ids or len(set(sample_ids)) != len(sample_ids) or not set(sample_ids) <= set(ids):
            raise ValueError("Unknown, empty or duplicate sample IDs.")
        selected = set(sample_ids)
        cases = [c for c in cases if c.id in selected]
    cases = cases[:limit]
    if isinstance(benchmark, FinanceBenchmark):
        await benchmark.validate()
    if not hasattr(agent, "invoke") and callable(agent):
        agent = CallableAgent(agent, timeout=timeout)
    target_description = agent.describe()
    target = _public({key: value for key, value in target_description.items() if key in _PUBLIC_TARGET_KEYS})
    target["kind"] = "external_agent"
    target["config"] = _public(target_config or {})
    target["config_sha256"] = _digest(target["config"])
    source = trace_source.describe() if trace_source is not None else {}
    source_description = _public({k: v for k, v in source.items() if k in _PUBLIC_TARGET_KEYS | {"workspace_id"}})
    contract = {
        "benchmark": benchmark.name,
        "description": description,
        "requires_trace": benchmark.requires_trace,
        "cases": [{"id": c.id, "sha256": _digest(c.model_dump())} for c in cases],
    }
    run_id = new_id("run")
    with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                workspace_id=workspace_id,
                benchmark=benchmark.name,
                variant_key=_digest(contract),
                model=None,
                target_kind="external_agent",
                target=target,
                status="running",
                started_at=now(),
                samples_total=len(cases),
                limit=limit,
                epochs=1,
                content_policy=content_policy,
                manifest={
                    "evaluation_contract": contract,
                    "trace_source": source_description,
                    "finance_evaluation": description
                    if isinstance(benchmark.name, str)
                    and benchmark.name in ("findeepindicator", "bigfinance-public-50")
                    else None,
                    "invocation": {
                        "timeout": timeout,
                        "trace_timeout": trace_timeout,
                        "grading_timeout": grading_timeout,
                        "retries": 0,
                    },
                },
            )
        )
    report = EvaluationReport(run_id=run_id, benchmark=benchmark.name, target=target, status="running", results=[])
    for case in cases:
        invocation = AgentInput(
            input=case.input,
            run_id=run_id,
            sample_id=case.id,
            trace_id=secrets.token_hex(16),
            parent_span_id=secrets.token_hex(8),
        )
        result = CaseResult(sample_id=case.id, attempt=1, status="unscored", trace_id=invocation.trace_id)
        started = time.monotonic()
        evidence, grade = None, None
        phase = "agent"
        # Detach ambient tracing: evaluator traffic must not become agent evidence.
        token = otel_context.attach(otel_context.Context())
        try:
            with session_scope() as session:
                if session.get(Run, run_id).status == "cancelling":
                    raise asyncio.CancelledError
            answer = AgentResult.model_validate(await _bounded(agent.invoke(invocation), timeout, run_id))
            if not answer.answer.strip() or answer.trace_id not in (None, invocation.trace_id):
                raise ValueError("Invalid completed agent result.")
            result.answer = answer.answer
            result.metadata["usage"] = {
                key: value
                for key, value in answer.usage.items()
                if key in ("input_tokens", "output_tokens") and type(value) is int and value >= 0
            }
            phase = "trace"
            try:
                evidence = await _bounded(
                    _trace(trace_source, invocation, trace_timeout), max(trace_timeout, 0.001) + 0.1, run_id
                )
                result.trace_state = evidence.state
            except Exception:
                result.trace_state = "missing"
            if benchmark.requires_trace and (evidence is None or evidence.state != "complete"):
                result.status, result.error = (
                    "trace_missing",
                    "Required trace missing, incomplete, withheld or inaccessible.",
                )
            else:
                phase = "grading"
                grade = GradeResult.model_validate(
                    await _bounded(benchmark.grade(case, answer, evidence), grading_timeout, run_id)
                )
                if description.get("protocol") is not None and grade.protocol != description["protocol"]:
                    raise ValueError("Grader protocol differs from its declared contract.")
                score = normalized_score(grade.score)
                components = {k: normalized_score(v) if v is not None else None for k, v in grade.components.items()}
                result.score, result.components = score, components
                result.explanation = grade.explanation
                result.metadata["protocol"] = grade.protocol
                if grade.metadata.get("format_repair") is True:
                    result.metadata["format_repair"] = True
                result.status = "scored"
        except asyncio.CancelledError:
            result.status, result.error = "cancelled", "Evaluation cancelled."
            result.duration_seconds = time.monotonic() - started
            report.results.append(_save_case(case, invocation, result, evidence, grade, content_policy, workspace_id))
            report.status = "cancelled"
            _finish(report)
            raise
        except TimeoutError as exc:
            if phase == "grading":
                _apply_grading_failure(result, exc, run_id, case.id, content_policy)
            else:
                result.status, result.error = "timeout", "Agent invocation timed out."
        except Exception as exc:
            if phase == "grading":
                _apply_grading_failure(result, exc, run_id, case.id, content_policy)
            else:
                result.status, result.error = "agent_error", "Agent invocation failed."
        finally:
            otel_context.detach(token)
        result.duration_seconds = time.monotonic() - started
        try:
            report.results.append(_save_case(case, invocation, result, evidence, grade, content_policy, workspace_id))
        except Exception:
            report.status = "failed"
            _finish(report)
            raise RuntimeError("Evaluation evidence persistence failed.") from None
    report.status = "succeeded" if all(r.status == "scored" for r in report.results) else "failed"
    return _finish(report)


def evaluate_sync(*args: Any, **kwargs: Any) -> EvaluationReport:
    """Synchronous entry point. In an event loop, await evaluate instead."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(evaluate(*args, **kwargs))
    raise RuntimeError("Use await evaluate(...) inside an event loop.")
