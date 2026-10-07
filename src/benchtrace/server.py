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
from fastapi.responses import FileResponse, JSONResponse, Response
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


class ImportRequest(BaseModel):
    source: str
    project: str | None = None
    max_traces: int = Field(default=100, ge=1, le=10000)
    usage_rights: str
    content_policy: str = "redacted"


class DatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class AddItemsRequest(BaseModel):
    trace_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    outcomes: list[str] = Field(default_factory=list)
    split: str = "unassigned"


class ReviewRequest(BaseModel):
    reference_output: str | None = None
    split: str | None = None
    usage_rights: str | None = None
    notes: str | None = None
    status: str | None = None


class SealRequest(BaseModel):
    expected_spans: int = Field(ge=0)


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

    # -- OTLP ingest

    ingest_policy = os.environ.get("BENCHTRACE_INGEST_CONTENT", "metadata")

    @app.post("/v1/traces")
    async def otlp_traces(request: Request, ws: WorkspaceContext = Depends(current_workspace)) -> Response:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

        from benchtrace.otlp import OTLPError, effective_policy, ingest

        content_type = request.headers.get("content-type", "application/x-protobuf")
        body = await request.body()
        if request.headers.get("content-encoding") == "gzip":
            import gzip

            body = gzip.decompress(body)
        policy = effective_policy(ingest_policy, request.headers.get("x-benchtrace-content"))
        try:
            with session_scope() as session:
                receipt = ingest(
                    session,
                    body,
                    content_type,
                    batch_id=request.headers.get("x-benchtrace-batch-id"),
                    policy=policy,
                    source=request.headers.get("x-benchtrace-source", "otlp")[:40],
                    workspace_id=ws.workspace_id,
                )
        except OTLPError as ex:
            raise HTTPException(400, str(ex)) from ex
        headers = {
            "x-benchtrace-accepted-spans": str(receipt["accepted_spans"]),
            "x-benchtrace-duplicate": str(receipt["duplicate"]).lower(),
            "x-benchtrace-content": policy,
        }
        if "json" in content_type:
            return JSONResponse({"partialSuccess": {}, "benchtrace": receipt}, headers=headers)
        return Response(
            ExportTraceServiceResponse().SerializeToString(), media_type="application/x-protobuf", headers=headers
        )

    @app.post("/api/traces/{trace_id}/seal")
    def seal_trace(trace_id: str, body: SealRequest, ws: WorkspaceContext = Depends(current_workspace)):
        from benchtrace.otlp import OTLPError, seal

        try:
            with session_scope() as session:
                return seal(session, trace_id, body.expected_spans, ws.workspace_id)
        except OTLPError as ex:
            raise HTTPException(409, str(ex)) from ex

    # -- imports

    import_max_content = os.environ.get("BENCHTRACE_IMPORT_MAX_CONTENT", "full")

    @app.post("/api/imports", status_code=201)
    def start_import(body: ImportRequest, ws: WorkspaceContext = Depends(current_workspace)) -> dict[str, Any]:
        from benchtrace.imports import IMPORTERS, create_import
        from benchtrace.otlp import effective_policy

        if body.source not in IMPORTERS:
            raise HTTPException(400, f"Source must be one of {', '.join(IMPORTERS)}; import files with the CLI.")
        try:
            run = create_import(
                body.source,
                project=body.project,
                usage_rights=body.usage_rights,
                content_policy=effective_policy(import_max_content, body.content_policy),
                options={"max_traces": body.max_traces},
                workspace_id=ws.workspace_id,
            )
        except ValueError as ex:
            raise HTTPException(400, str(ex)) from ex
        enqueue("import", run.id)
        return row_dict(run)

    @app.get("/api/imports")
    def list_imports(ws: WorkspaceContext = Depends(current_workspace)) -> list[dict[str, Any]]:
        from benchtrace.db import ImportRun

        with session_scope() as session:
            rows = session.scalars(
                select(ImportRun)
                .where(ImportRun.workspace_id == ws.workspace_id)
                .order_by(ImportRun.created_at.desc())
                .limit(100)
            ).all()
            return [row_dict(r) for r in rows]

    @app.get("/api/imports/{import_id}")
    def get_import(import_id: str, ws: WorkspaceContext = Depends(current_workspace)) -> dict[str, Any]:
        from benchtrace.db import ImportRun

        with session_scope() as session:
            run = session.get(ImportRun, import_id)
            if run is None or run.workspace_id != ws.workspace_id:
                raise HTTPException(404, "Import not found")
            return row_dict(run)

    # -- datasets

    def _get_dataset(session, dataset_id: str, ws: WorkspaceContext):
        from benchtrace.db import Dataset

        ds = session.get(Dataset, dataset_id)
        if ds is None or ds.workspace_id != ws.workspace_id:
            raise HTTPException(404, "Dataset not found")
        return ds

    @app.post("/api/datasets", status_code=201)
    def new_dataset(body: DatasetRequest, ws: WorkspaceContext = Depends(current_workspace)) -> dict[str, Any]:
        from benchtrace.datasets import create_dataset

        return row_dict(create_dataset(body.name, body.description, ws.workspace_id))

    @app.get("/api/datasets")
    def list_datasets(ws: WorkspaceContext = Depends(current_workspace)) -> list[dict[str, Any]]:
        from benchtrace.db import Dataset, DatasetItem

        with session_scope() as session:
            rows = session.scalars(
                select(Dataset).where(Dataset.workspace_id == ws.workspace_id).order_by(Dataset.created_at.desc())
            ).all()
            out = []
            for ds in rows:
                counts = dict(
                    session.execute(
                        select(DatasetItem.status, func.count(DatasetItem.id))
                        .where(DatasetItem.dataset_id == ds.id)
                        .group_by(DatasetItem.status)
                    ).all()
                )
                out.append(row_dict(ds) | {"counts": counts})
            return out

    @app.get("/api/datasets/{dataset_id}")
    def get_dataset(dataset_id: str, ws: WorkspaceContext = Depends(current_workspace)) -> dict[str, Any]:
        from benchtrace.db import DatasetItem

        with session_scope() as session:
            ds = _get_dataset(session, dataset_id, ws)
            items = session.scalars(
                select(DatasetItem).where(DatasetItem.dataset_id == dataset_id).order_by(DatasetItem.id)
            ).all()
            return row_dict(ds) | {"items": [row_dict(i) for i in items]}

    @app.post("/api/datasets/{dataset_id}/items", status_code=201)
    def add_items(dataset_id: str, body: AddItemsRequest, ws: WorkspaceContext = Depends(current_workspace)):
        from benchtrace.datasets import ReviewError, add_run_samples, add_traces

        with session_scope() as session:
            _get_dataset(session, dataset_id, ws)
            if body.run_id:
                _get_run(session, body.run_id, ws)
        try:
            items = add_traces(dataset_id, body.trace_ids, body.split) if body.trace_ids else []
            if body.run_id:
                items += add_run_samples(dataset_id, body.run_id, body.outcomes, body.split)
        except LookupError as ex:
            raise HTTPException(404, str(ex)) from ex
        except ReviewError as ex:
            raise HTTPException(400, str(ex)) from ex
        return [row_dict(i) for i in items]

    @app.patch("/api/datasets/{dataset_id}/items/{item_id}")
    def review(dataset_id: str, item_id: int, body: ReviewRequest, ws: WorkspaceContext = Depends(current_workspace)):
        from benchtrace.datasets import ReviewError, review_item
        from benchtrace.db import DatasetItem

        with session_scope() as session:
            _get_dataset(session, dataset_id, ws)
            item = session.get(DatasetItem, item_id)
            if item is None or item.dataset_id != dataset_id:
                raise HTTPException(404, "Item not found")
        try:
            return row_dict(review_item(item_id, reviewer=ws.user, **body.model_dump()))
        except ReviewError as ex:
            raise HTTPException(400, str(ex)) from ex

    @app.get("/api/datasets/{dataset_id}/export")
    def export_dataset(dataset_id: str, split: str | None = None, ws: WorkspaceContext = Depends(current_workspace)):
        import json as _json

        from benchtrace.datasets import export_items

        with session_scope() as session:
            ds = _get_dataset(session, dataset_id, ws)
            name = ds.name
        lines = "".join(_json.dumps(row, ensure_ascii=False) + "\n" for row in export_items(dataset_id, split))
        filename = "".join(c if c.isalnum() or c in "-_" else "-" for c in name) or "dataset"
        return Response(
            lines,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{filename}.jsonl"'},
        )

    @app.get("/api/trace-diff")
    def trace_diff(a: str, b: str, ws: WorkspaceContext = Depends(current_workspace)) -> dict[str, Any]:
        from benchtrace.trace_diff import diff_traces

        with session_scope() as session:
            try:
                result = diff_traces(session, a, b)
            except LookupError as ex:
                raise HTTPException(404, str(ex)) from ex
            if result.a["workspace_id"] != ws.workspace_id or result.b["workspace_id"] != ws.workspace_id:
                raise HTTPException(404, "Trace not found")
            return result.to_dict()

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
