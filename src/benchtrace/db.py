"""Database models and session handling.

The same schema runs on SQLite (CLI) and Postgres (server).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from benchtrace.config import settings


def now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Run(Base):
    """One model evaluated on one catalog benchmark."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("run"))
    workspace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    group_id: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # queued | running | succeeded | failed | cancelled | budget_exceeded
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    benchmark: Mapped[str] = mapped_column(String(100), index=True)
    variant_key: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    resolved_models: Mapped[list[Any]] = mapped_column(default=list)
    limit: Mapped[int | None] = mapped_column(Integer)
    epochs: Mapped[int] = mapped_column(Integer, default=1)
    budget_usd: Mapped[float | None] = mapped_column(Float)
    content_policy: Mapped[str] = mapped_column(String(20), default="full")
    quote_id: Mapped[str | None] = mapped_column(String(32))
    samples_total: Mapped[int | None] = mapped_column(Integer)
    samples_done: Mapped[int] = mapped_column(Integer, default=0)
    n_correct: Mapped[int] = mapped_column(Integer, default=0)
    n_error: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    log_path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class SampleResult(Base):
    """Outcome of one task (sample) in a run."""

    __tablename__ = "samples"
    __table_args__ = (Index("ix_samples_run_sample", "run_id", "sample_id", "epoch", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    sample_id: Mapped[str] = mapped_column(String(200))
    epoch: Mapped[int] = mapped_column(Integer, default=1)
    # correct | incorrect | partial | error | unscored | cancelled (interrupted by a stop)
    outcome: Mapped[str] = mapped_column(String(20))
    score: Mapped[float | None] = mapped_column(Float)
    score_raw: Mapped[str | None] = mapped_column(Text)
    scorer: Mapped[str | None] = mapped_column(String(200))
    answer: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(Text)
    input: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_time: Mapped[float | None] = mapped_column(Float)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)


class Span(Base):
    """A normalized trace span from any source (benchmark run, SDK, OTLP, import)."""

    __tablename__ = "spans"
    __table_args__ = (Index("ix_spans_trace_span", "trace_id", "span_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    span_id: Mapped[str] = mapped_column(String(64))
    parent_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(300))
    # agent | model | tool | scorer | solver | sandbox | error | span | unknown
    kind: Mapped[str] = mapped_column(String(20))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="ok")
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)
    content: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # present | withheld | redacted | missing
    content_state: Mapped[str] = mapped_column(String(10), default="present")
    source: Mapped[str] = mapped_column(String(40), default="inspect")


class Trace(Base):
    """Trace header. Benchmark traces link to a run and sample; others stand alone."""

    __tablename__ = "traces"

    trace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    sample_id: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str | None] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(40), default="inspect")
    source_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    span_count: Mapped[int] = mapped_column(Integer, default=0)
    # open | closed: closed means all producer-declared spans were received
    state: Mapped[str] = mapped_column(String(10), default="closed")
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Quote(Base):
    """Cost estimate for a planned run, approved by a person before paid work."""

    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("quote"))
    workspace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    benchmark: Mapped[str] = mapped_column(String(100))
    models: Mapped[list[Any]] = mapped_column(default=list)
    limit: Mapped[int | None] = mapped_column(Integer)
    sample_size: Mapped[int] = mapped_column(Integer)
    samples_planned: Mapped[int | None] = mapped_column(Integer)
    estimate: Mapped[dict[str, Any]] = mapped_column(default=dict)
    cap_usd: Mapped[float | None] = mapped_column(Float)
    # estimating | draft | approved | used | failed
    status: Mapped[str] = mapped_column(String(10), default="draft")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(200))


class Job(Base):
    """Background work item, claimed by workers with an atomic conditional update."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("job"))
    kind: Mapped[str] = mapped_column(String(20))  # run | quote
    target_id: Mapped[str] = mapped_column(String(32), index=True)
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(10), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker: Mapped[str | None] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


def get_engine(url: str | None = None) -> Engine:
    cfg = settings()
    url = url or cfg.database_url
    if url.startswith("sqlite"):
        cfg.ensure_dirs()
    return _engine(url)


@lru_cache(maxsize=8)
def _engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _sqlite_pragmas)
    else:
        engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    factory = sessionmaker(bind=get_engine(url), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
