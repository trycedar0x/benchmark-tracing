"""Database-backed job queue and worker.

Jobs are claimed with one conditional UPDATE, which is atomic on both SQLite
and Postgres: concurrent workers racing for the same row see at most one
successful update. No Redis or queue library is needed.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
from datetime import timedelta

from sqlalchemy import select, text, update

from benchtrace.db import Job, Quote, Run, get_engine, now, session_scope
from benchtrace.execution import TERMINAL, execute_run

log = logging.getLogger("benchtrace.worker")

HEARTBEAT_SECONDS = 10
STALE_AFTER = timedelta(seconds=90)


def enqueue(kind: str, target_id: str) -> Job:
    with session_scope() as session:
        job = Job(kind=kind, target_id=target_id)
        session.add(job)
        session.flush()
    return job


def claim(worker: str) -> Job | None:
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                "UPDATE jobs SET status = 'running', worker = :worker, attempts = attempts + 1 "
                "WHERE status = 'queued' AND id = ("
                "  SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
                ") RETURNING id"
            ),
            {"worker": worker},
        ).first()
    if row is None:
        return None
    with session_scope() as session:
        job = session.get(Job, row[0])
        job.started_at = job.heartbeat_at = now()
        return job


def heartbeat(job_id: str) -> None:
    with session_scope() as session:
        session.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=now()))


def finish(job_id: str, error: str | None = None) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = "failed" if error else "done"
        job.error = error
        job.finished_at = now()


def recover_stale() -> int:
    """Fail jobs whose worker stopped heartbeating, and their runs.

    Paid runs are not retried automatically: a lost worker may have spent money.
    """
    cutoff = now() - STALE_AFTER
    recovered = 0
    with session_scope() as session:
        stale = session.scalars(select(Job).where(Job.status == "running", Job.heartbeat_at < cutoff)).all()
        for job in stale:
            job.status = "failed"
            job.error = f"Worker {job.worker} stopped responding."
            job.finished_at = now()
            if job.kind == "run":
                run = session.get(Run, job.target_id)
                if run and run.status not in TERMINAL:
                    run.status = "failed"
                    run.error = f"Worker {job.worker} stopped responding; partial results kept."
                    run.finished_at = now()
            elif job.kind == "import":
                from benchtrace.db import ImportRun

                imp = session.get(ImportRun, job.target_id)
                if imp and imp.status in ("queued", "running"):
                    imp.status = "failed"
                    imp.error = f"Worker {job.worker} stopped responding."
            elif job.kind == "quote":
                quote = session.get(Quote, job.target_id)
                if quote and quote.status == "estimating":
                    quote.status = "failed"
            recovered += 1
    return recovered


def process(job: Job) -> None:
    if job.kind == "run":
        execute_run(job.target_id)
    elif job.kind == "quote":
        from benchtrace.service import compute_quote

        try:
            compute_quote(job.target_id)
        except Exception:
            with session_scope() as session:
                quote = session.get(Quote, job.target_id)
                if quote:
                    quote.status = "failed"
            raise
    elif job.kind == "import":
        from benchtrace.imports import execute_import

        execute_import(job.target_id)
    else:
        raise ValueError(f"Unknown job kind {job.kind!r}")


class Worker:
    """Runs jobs from the queue with N concurrent slots."""

    def __init__(self, concurrency: int = 2, poll_seconds: float = 1.0) -> None:
        self.name = f"{socket.gethostname()}:{os.getpid()}"
        self.concurrency = concurrency
        self.poll_seconds = poll_seconds
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def _slot(self, index: int) -> None:
        name = f"{self.name}/{index}"
        while not self.stop.is_set():
            try:
                job = claim(name)
            except Exception:  # noqa: BLE001 - database hiccups should not kill the worker
                log.exception("claim failed")
                job = None
            if job is None:
                self.stop.wait(self.poll_seconds)
                continue
            log.info("job %s (%s %s) started", job.id, job.kind, job.target_id)
            beating = threading.Event()

            def beat(job_id: str = job.id, done: threading.Event = beating) -> None:
                while not done.wait(HEARTBEAT_SECONDS):
                    try:
                        heartbeat(job_id)
                    except Exception:  # noqa: BLE001
                        log.exception("heartbeat failed")

            beater = threading.Thread(target=beat, daemon=True)
            beater.start()
            error = None
            try:
                process(job)
            except Exception as ex:  # noqa: BLE001 - record any failure on the job
                error = f"{type(ex).__name__}: {ex}\n{traceback.format_exc(limit=5)}"
                log.exception("job %s failed", job.id)
            finally:
                beating.set()
            finish(job.id, error)
            log.info("job %s finished%s", job.id, " with error" if error else "")

    def _janitor(self) -> None:
        while not self.stop.wait(30):
            try:
                if count := recover_stale():
                    log.warning("recovered %d stale job(s)", count)
            except Exception:  # noqa: BLE001
                log.exception("stale job recovery failed")

    def start(self) -> None:
        recover_stale()
        self.threads = [
            threading.Thread(target=self._slot, args=(i,), daemon=True, name=f"worker-{i}")
            for i in range(self.concurrency)
        ]
        self.threads.append(threading.Thread(target=self._janitor, daemon=True, name="janitor"))
        for t in self.threads:
            t.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop claiming jobs and wait briefly for running ones."""
        self.stop.set()
        deadline = time.monotonic() + timeout
        for t in self.threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))

    def run_forever(self) -> None:
        self.start()
        try:
            while not self.stop.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop.set()
