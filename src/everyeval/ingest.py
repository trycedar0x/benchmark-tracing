"""Write converted results and spans to the database.

All writes are idempotent: re-ingesting a sample replaces its result and spans,
and run totals are recomputed from stored samples rather than incremented.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from everyeval.db import Run, SampleResult, Span, Trace
from everyeval.pricing import cost_for
from everyeval.redact import apply_policy, redact, scrub_attributes

_CONTENT_FIELDS = ("answer", "explanation", "target", "input", "output")


def store_spans(
    session: Session,
    trace: dict[str, Any],
    spans: list[dict[str, Any]],
    *,
    policy: str,
    source: str,
    run_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    trace_id = trace["trace_id"]
    session.execute(delete(Span).where(Span.trace_id == trace_id))
    existing = session.get(Trace, trace_id)
    if existing is not None:
        session.delete(existing)
        session.flush()
    session.add(
        Trace(
            workspace_id=workspace_id,
            run_id=run_id,
            source=source,
            **{k: v for k, v in trace.items() if k in Trace.__table__.columns},
        )
    )
    for raw in spans:
        content, state = apply_policy(raw.get("content"), policy)
        session.add(
            Span(
                workspace_id=workspace_id,
                trace_id=trace_id,
                run_id=run_id,
                source=source,
                span_id=raw["span_id"],
                parent_id=raw.get("parent_id"),
                name=raw["name"][:300],
                kind=raw["kind"],
                start_time=raw.get("start_time"),
                end_time=raw.get("end_time"),
                status=raw.get("status", "ok"),
                attributes=scrub_attributes(raw.get("attributes") or {}),
                content=content,
                content_state=state,
            )
        )


def store_sample(session: Session, run: Run, converted: dict[str, Any]) -> None:
    result = dict(converted["result"])
    if run.content_policy == "metadata":
        for field in _CONTENT_FIELDS:
            result[field] = None
    elif run.content_policy == "redacted":
        for field in _CONTENT_FIELDS:
            result[field] = redact(result[field])
    if result.get("error"):
        result["error"] = redact(result["error"])

    session.execute(
        delete(SampleResult).where(
            SampleResult.run_id == run.id,
            SampleResult.sample_id == result["sample_id"],
            SampleResult.epoch == result["epoch"],
        )
    )
    session.add(SampleResult(run_id=run.id, **result))
    store_spans(
        session,
        converted["trace"],
        converted["spans"],
        policy=run.content_policy,
        source="inspect",
        run_id=run.id,
        workspace_id=run.workspace_id,
    )
    models = set(run.resolved_models or []) | converted["resolved_models"]
    run.resolved_models = sorted(models)
    session.flush()
    refresh_totals(session, run)


def refresh_totals(session: Session, run: Run) -> None:
    row = session.execute(
        select(
            func.count(SampleResult.id),
            func.sum(case((SampleResult.outcome == "correct", 1), else_=0)),
            func.sum(case((SampleResult.outcome == "error", 1), else_=0)),
            func.coalesce(func.sum(SampleResult.input_tokens), 0),
            func.coalesce(func.sum(SampleResult.output_tokens), 0),
        ).where(SampleResult.run_id == run.id, SampleResult.outcome != "cancelled")
    ).one()
    run.samples_done = row[0] or 0
    run.n_correct = row[1] or 0
    run.n_error = row[2] or 0
    run.input_tokens = row[3] or 0
    run.output_tokens = row[4] or 0
    run.cost_usd = cost_for(run.model, run.input_tokens, run.output_tokens)
