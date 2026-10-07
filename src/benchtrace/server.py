"""HTTP API and web UI.

The API wraps the same service functions the CLI uses. Runs and quotes are
executed by workers through the job queue, so requests return immediately.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from benchtrace.catalog import CatalogError, get_benchmark, load_catalog
from benchtrace.compare import compare_runs
from benchtrace.db import Base, Job, Quote, Run, SampleResult, Span, Trace, get_engine, session_scope
from benchtrace.execution import request_cancel
from benchtrace.jobs import Worker, enqueue
from benchtrace.pricing import load_prices
from benchtrace.service import (
    MOCK_PREFIX,
    PlanError,
    approve_quote,
    create_quote_record,
    create_runs,
    is_paid,
    runs_from_quote,
)

# ---------------------------------------------------------------------------- helpers


def row_dict(obj: Base, exclude: tuple[str, ...] = ()) -> dict[str, Any]:
    out = {}
    for column in obj.__table__.columns:
        if column.name in exclude:
            continue
        value = getattr(obj, column.name)
        out[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return out


def run_dict(run: Run, full: bool = False) -> dict[str, Any]:
    data = row_dict(run, exclude=() if full else ("manifest",))
    scored = run.samples_done - run.n_error
    data["accuracy"] = run.n_correct / scored if scored else None
    data["purpose"] = (run.manifest or {}).get("purpose", "eval")
    return data


class WorkspaceContext(BaseModel):
    workspace_id: str | None = None
    user: str = "local"


def current_workspace(request: Request) -> WorkspaceContext:
    """Single-workspace mode until auth is enabled."""
    return WorkspaceContext()


# ---------------------------------------------------------------------------- request models


class QuoteRequest(BaseModel):
    benchmark: str
    models: list[str] = Field(min_length=1)
    limit: int | None = Field(default=None, ge=1)
    sample_size: int = Field(default=5, ge=1, le=50)


class ApproveRequest(BaseModel):
    cap_usd: float | None = Field(default=None, gt=0)


class RunRequest(BaseModel):
    benchmark: str | None = None
    models: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)
    epochs: int = Field(default=1, ge=1, le=100)
    budget_usd: float | None = Field(default=None, gt=0)
    content_policy: str = "full"
    quote_id: str | None = None


# ---------------------------------------------------------------------------- app


def create_app(workers: int = 0) -> FastAPI:
    worker = Worker(concurrency=workers) if workers > 0 else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        get_engine()
        if worker:
            worker.start()
        yield
        if worker:
            worker.shutdown()

    app = FastAPI(title="benchtrace", version="0.1.0", lifespan=lifespan)
    require_quote = os.environ.get("BENCHTRACE_REQUIRE_QUOTE", "1") != "0"

    @app.exception_handler(PlanError)
    async def plan_error(_: Request, ex: PlanError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(ex)})

    @app.exception_handler(CatalogError)
    async def catalog_error(_: Request, ex: CatalogError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(ex)})

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        with session_scope() as session:
            queued = session.scalar(select(func.count(Job.id)).where(Job.status == "queued"))
        return {"ok": True, "queued_jobs": queued, "embedded_workers": workers}

    # -- catalog and models

    @app.get("/api/catalog")
    def catalog() -> list[dict[str, Any]]:
        entries = sorted(load_catalog().values(), key=lambda e: (not e.offline, e.id))
        return [e.model_dump() | {"ref": e.ref, "variant_key": e.variant_key} for e in entries]

    @app.get("/api/catalog/{ref}")
    def catalog_entry(ref: str) -> dict[str, Any]:
        entry = get_benchmark(ref)
        return entry.model_dump() | {"ref": entry.ref, "variant_key": entry.variant_key}

    @app.get("/api/models")
    def models() -> list[dict[str, Any]]:
        return [
            {"model": name, "price": price.model_dump(), "mock": name.startswith(MOCK_PREFIX)}
            for name, price in sorted(load_prices().items())
        ]

    # -- quotes

    @app.post("/api/quotes", status_code=201)
    def create_quote(body: QuoteRequest, ws: WorkspaceContext = Depends(current_workspace)) -> dict[str, Any]:
        entry = get_benchmark(body.benchmark)
        quote = create_quote_record(
            entry, body.models, limit=body.limit, sample_size=body.sample_size, workspace_id=ws.workspace_id
        )
        enqueue("quote", quote.id)
        return row_dict(quote)

    @app.get("/api/quotes/{quote_id}")
    def get_quote(quote_id: str, ws: WorkspaceContext = Depends(current_workspace)) -> dict[str, Any]:
        with session_scope() as session:
            quote = session.get(Quote, quote_id)
            if quote is None or quote.workspace_id != ws.workspace_id:
                raise HTTPException(404, "Quote not found")
            return row_dict(quote)

    @app.post("/api/quotes/{quote_id}/approve")
    def approve(quote_id: str, body: ApproveRequest, ws: WorkspaceContext = Depends(current_workspace)):
        with session_scope() as session:
            quote = session.get(Quote, quote_id)
            if quote is None or quote.workspace_id != ws.workspace_id:
                raise HTTPException(404, "Quote not found")
            if quote.status != "draft":
                raise HTTPException(409, f"Quote is {quote.status}; only an estimated draft can be approved.")
        return row_dict(approve_quote(quote_id, body.cap_usd, ws.user))

    # -- runs

    @app.post("/api/runs", status_code=201)
    def start_runs(body: RunRequest, ws: WorkspaceContext = Depends(current_workspace)) -> list[dict[str, Any]]:
        if body.quote_id:
            with session_scope() as session:
                quote = session.get(Quote, body.quote_id)
                if quote is None or quote.workspace_id != ws.workspace_id:
                    raise HTTPException(404, "Quote not found")
            runs = runs_from_quote(body.quote_id, content_policy=body.content_policy, epochs=body.epochs)
        else:
            if not body.benchmark or not body.models:
                raise HTTPException(400, "Give a benchmark and at least one model, or a quote_id.")
            paid = [m for m in body.models if is_paid(m)]
            if paid and require_quote:
                raise HTTPException(
                    400, f"Paid models ({', '.join(paid)}) need an approved quote. Create one at /api/quotes."
                )
            runs = create_runs(
                get_benchmark(body.benchmark),
                body.models,
                limit=body.limit,
                epochs=body.epochs,
                budget_usd=body.budget_usd,
                content_policy=body.content_policy,
                workspace_id=ws.workspace_id,
            )
        for run in runs:
            enqueue("run", run.id)
        return [run_dict(r) for r in runs]

    @app.get("/api/runs")
    def list_runs(
        ws: WorkspaceContext = Depends(current_workspace),
        benchmark: str | None = None,
        status: str | None = None,
        include_quotes: bool = False,
        limit: int = Query(default=50, le=500),
    ) -> list[dict[str, Any]]:
        with session_scope() as session:
            query = select(Run).where(Run.workspace_id == ws.workspace_id)
            if benchmark:
                query = query.where(Run.benchmark == benchmark)
            if status:
                query = query.where(Run.status == status)
            runs = session.scalars(query.order_by(Run.created_at.desc()).limit(limit * 2)).all()
            out = [run_dict(r) for r in runs]
        if not include_quotes:
            out = [r for r in out if r["purpose"] != "quote"]
        return out[:limit]

    def _get_run(session, run_id: str, ws: WorkspaceContext) -> Run:
        run = session.get(Run, run_id)
        if run is None or run.workspace_id != ws.workspace_id:
            raise HTTPException(404, "Run not found")
        return run

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, ws: WorkspaceContext = Depends(current_workspace)) -> dict[str, Any]:
        with session_scope() as session:
            run = _get_run(session, run_id, ws)
            data = run_dict(run, full=True)
            counts = dict(
                session.execute(
                    select(SampleResult.outcome, func.count(SampleResult.id))
                    .where(SampleResult.run_id == run_id)
                    .group_by(SampleResult.outcome)
                ).all()
            )
        return data | {"outcomes": counts}

    @app.get("/api/runs/{run_id}/samples")
    def run_samples(
        run_id: str,
        ws: WorkspaceContext = Depends(current_workspace),
        outcome: str | None = None,
        offset: int = 0,
        limit: int = Query(default=100, le=1000),
    ) -> dict[str, Any]:
        with session_scope() as session:
            _get_run(session, run_id, ws)
            query = select(SampleResult).where(SampleResult.run_id == run_id)
            if outcome:
                query = query.where(SampleResult.outcome == outcome)
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.scalars(
                query.order_by(SampleResult.sample_id, SampleResult.epoch).offset(offset).limit(limit)
            ).all()
            return {"total": total, "items": [row_dict(s) for s in rows]}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str, ws: WorkspaceContext = Depends(current_workspace)) -> dict[str, Any]:
        with session_scope() as session:
            _get_run(session, run_id, ws)
        return run_dict(request_cancel(run_id))

    # -- compare and traces

    @app.get("/api/compare")
    def compare(a: str, b: str, force: bool = False, ws: WorkspaceContext = Depends(current_workspace)):
        with session_scope() as session:
            _get_run(session, a, ws)
            _get_run(session, b, ws)
        return compare_runs(a, b, force=force).to_dict()

    @app.get("/api/traces")
    def list_traces(
        ws: WorkspaceContext = Depends(current_workspace),
        run_id: str | None = None,
        source: str | None = None,
        limit: int = Query(default=100, le=1000),
    ) -> list[dict[str, Any]]:
        with session_scope() as session:
            query = select(Trace).where(Trace.workspace_id == ws.workspace_id)
            if run_id:
                query = query.where(Trace.run_id == run_id)
            if source:
                query = query.where(Trace.source == source)
            rows = session.scalars(query.order_by(Trace.created_at.desc()).limit(limit)).all()
            return [row_dict(t) for t in rows]

    @app.get("/api/traces/{trace_id}")
    def get_trace(trace_id: str, ws: WorkspaceContext = Depends(current_workspace)) -> dict[str, Any]:
        with session_scope() as session:
            header = session.get(Trace, trace_id)
            if header is None or header.workspace_id != ws.workspace_id:
                raise HTTPException(404, "Trace not found")
            spans = session.scalars(select(Span).where(Span.trace_id == trace_id).order_by(Span.start_time, Span.id))
            sample = None
            if header.run_id:
                sample = session.scalars(select(SampleResult).where(SampleResult.trace_id == trace_id)).first()
            return {
                "trace": row_dict(header),
                "sample": row_dict(sample) if sample else None,
                "spans": [row_dict(s, exclude=("id", "workspace_id")) for s in spans],
            }

    # -- web UI

    static_dir = Path(str(resources.files("benchtrace") / "static"))
    index = static_dir / "index.html"
    if index.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = static_dir / path
            if path and candidate.is_file() and static_dir in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(index)
    else:

        @app.get("/", include_in_schema=False)
        def no_ui() -> JSONResponse:
            return JSONResponse(
                {"detail": "Web UI not built. Run `npm --prefix web ci && npm --prefix web run build`; API at /api."}
            )

    return app
