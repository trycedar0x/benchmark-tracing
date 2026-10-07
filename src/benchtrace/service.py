"""Operations shared by the CLI and the API server."""

from __future__ import annotations

import math
import statistics

from sqlalchemy import select

from benchtrace.catalog import BenchmarkEntry, get_benchmark
from benchtrace.db import Quote, Run, SampleResult, new_id, now, session_scope
from benchtrace.execution import execute_run
from benchtrace.pricing import price_for
from benchtrace.redact import POLICIES

MOCK_PREFIX = "btmock/"


class PlanError(ValueError):
    pass


def is_paid(model: str) -> bool:
    return not model.startswith(MOCK_PREFIX)


def planned_samples(entry: BenchmarkEntry, limit: int | None, epochs: int) -> int | None:
    if entry.task_count is None:
        return None if limit is None else limit * epochs
    return min(limit or entry.task_count, entry.task_count) * epochs


def validate_plan(entry: BenchmarkEntry, models: list[str], budget_usd: float | None, content_policy: str) -> None:
    if not models:
        raise PlanError("Choose at least one model with --model.")
    if content_policy not in POLICIES:
        raise PlanError(f"Content policy must be one of {', '.join(POLICIES)}.")
    if budget_usd is not None:
        missing = [m for m in models if price_for(m) is None]
        if missing:
            raise PlanError(
                f"No price for {', '.join(missing)}, so a budget cap cannot be enforced. "
                "Add it to ~/.benchtrace/pricing.yaml or run without a cap."
            )
    if entry.sandbox == "docker":
        import shutil

        if shutil.which("docker") is None:
            raise PlanError(f"{entry.ref} runs code in a Docker sandbox; install Docker first.")


def create_runs(
    entry: BenchmarkEntry,
    models: list[str],
    *,
    limit: int | None = None,
    epochs: int = 1,
    budget_usd: float | None = None,
    content_policy: str = "full",
    quote_id: str | None = None,
    workspace_id: str | None = None,
    purpose: str = "eval",
) -> list[Run]:
    validate_plan(entry, models, budget_usd, content_policy)
    group = new_id("grp")
    runs = []
    with session_scope() as session:
        for model in models:
            run = Run(
                workspace_id=workspace_id,
                group_id=group,
                benchmark=entry.ref,
                variant_key=entry.variant_key,
                model=model,
                limit=limit,
                epochs=epochs,
                budget_usd=budget_usd,
                content_policy=content_policy,
                quote_id=quote_id,
                samples_total=planned_samples(entry, limit, epochs),
                manifest={
                    "purpose": purpose,
                    "benchmark": entry.model_dump(),
                    "requested_model": model,
                    "limit": limit,
                    "epochs": epochs,
                    "content_policy": content_policy,
                },
            )
            session.add(run)
            runs.append(run)
        session.flush()
    return runs


def create_quote_record(
    entry: BenchmarkEntry,
    models: list[str],
    *,
    limit: int | None,
    epochs: int = 1,
    sample_size: int = 5,
    workspace_id: str | None = None,
) -> Quote:
    """Record a quote to be estimated. `compute_quote` fills in the estimate."""
    validate_plan(entry, models, None, "metadata")
    with session_scope() as session:
        quote = Quote(
            workspace_id=workspace_id,
            benchmark=entry.ref,
            models=models,
            limit=limit,
            sample_size=sample_size,
            samples_planned=planned_samples(entry, limit, epochs),
            status="estimating",
        )
        session.add(quote)
        session.flush()
    return quote


def compute_quote(quote_id: str) -> Quote:
    """Run a small sample per model and extrapolate tokens and cost to the planned run.

    The sample run is a real run: with paid models it costs money.
    """
    with session_scope() as session:
        quote = session.get(Quote, quote_id)
        entry, models, planned = get_benchmark(quote.benchmark), list(quote.models), quote.samples_planned
        sample_size, workspace_id = quote.sample_size, quote.workspace_id
    sample_runs = create_runs(
        entry,
        models,
        limit=sample_size,
        epochs=1,
        content_policy="metadata",
        workspace_id=workspace_id,
        purpose="quote",
    )
    estimate: dict[str, dict] = {}
    for run in sample_runs:
        finished = execute_run(run.id)
        with session_scope() as session:
            samples = session.scalars(select(SampleResult).where(SampleResult.run_id == run.id)).all()
            ok = [s for s in samples if s.outcome not in ("error", "cancelled")]
        estimate[run.model] = _extrapolate(run.model, ok, planned) | {
            "sample_run_id": run.id,
            "sample_status": finished.status,
            "sample_errors": len(samples) - len(ok),
            "sample_cost_usd": finished.cost_usd,
        }
    with session_scope() as session:
        quote = session.get(Quote, quote_id)
        quote.estimate = estimate
        quote.status = "draft"
    return quote


def create_quote(
    entry: BenchmarkEntry,
    models: list[str],
    *,
    limit: int | None,
    epochs: int = 1,
    sample_size: int = 5,
    workspace_id: str | None = None,
) -> Quote:
    """Create and estimate a quote synchronously (CLI)."""
    quote = create_quote_record(
        entry, models, limit=limit, epochs=epochs, sample_size=sample_size, workspace_id=workspace_id
    )
    return compute_quote(quote.id)


def _extrapolate(model: str, samples: list[SampleResult], planned: int | None) -> dict:
    n = len(samples)
    if n == 0:
        return {"samples_measured": 0, "note": "No successful sample calls; cannot estimate."}
    per_in = [s.input_tokens for s in samples]
    per_out = [s.output_tokens for s in samples]
    mean_in, mean_out = statistics.fmean(per_in), statistics.fmean(per_out)
    sd_in = statistics.stdev(per_in) if n > 1 else mean_in
    sd_out = statistics.stdev(per_out) if n > 1 else mean_out
    out: dict = {"samples_measured": n, "mean_input_tokens": mean_in, "mean_output_tokens": mean_out}
    if planned is None:
        return out | {"note": "Benchmark size unknown; per-sample estimate only."}
    # 95% interval on the mean, scaled to the planned number of samples.
    half_in = 1.96 * sd_in / math.sqrt(n) * planned
    half_out = 1.96 * sd_out / math.sqrt(n) * planned
    out |= {
        "input_tokens": mean_in * planned,
        "output_tokens": mean_out * planned,
        "input_tokens_range": [max(0.0, mean_in * planned - half_in), mean_in * planned + half_in],
        "output_tokens_range": [max(0.0, mean_out * planned - half_out), mean_out * planned + half_out],
    }
    price = price_for(model)
    if price:
        out |= {
            "cost_usd": price.cost(out["input_tokens"], out["output_tokens"]),
            "cost_usd_range": [
                price.cost(out["input_tokens_range"][0], out["output_tokens_range"][0]),
                price.cost(out["input_tokens_range"][1], out["output_tokens_range"][1]),
            ],
            "price": price.model_dump(),
        }
    else:
        out["note"] = "No price configured for this model; tokens only."
    return out


def approve_quote(quote_id: str, cap_usd: float | None, approved_by: str) -> Quote:
    with session_scope() as session:
        quote = session.get(Quote, quote_id)
        if quote is None:
            raise LookupError(f"Quote {quote_id} not found")
        quote.cap_usd = cap_usd
        quote.status = "approved"
        quote.approved_at = now()
        quote.approved_by = approved_by
        return quote


def runs_from_quote(quote_id: str, content_policy: str = "full", epochs: int = 1) -> list[Run]:
    with session_scope() as session:
        quote = session.get(Quote, quote_id)
        if quote is None:
            raise LookupError(f"Quote {quote_id} not found")
        if quote.status != "approved":
            raise PlanError(f"Quote {quote_id} is {quote.status}; approve it before running.")
        quote.status = "used"
        benchmark, models, limit, cap = quote.benchmark, list(quote.models), quote.limit, quote.cap_usd
        workspace_id = quote.workspace_id
    per_run_cap = None if cap is None else cap / len(models)
    return create_runs(
        get_benchmark(benchmark),
        models,
        limit=limit,
        epochs=epochs,
        budget_usd=per_run_cap,
        content_policy=content_policy,
        quote_id=quote_id,
        workspace_id=workspace_id,
    )
