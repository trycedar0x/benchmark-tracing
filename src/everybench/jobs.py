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
from datetime import timedelta

from sqlalchemy import or_, select, update

from everybench.db import Job, Run, get_engine, now, session_scope
from everybench.execution import TERMINAL, execute_run

log = logging.getLogger("everybench.worker")

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
        candidate = select(Job.id).where(Job.status == "queued").order_by(Job.created_at).limit(1).scalar_subquery()
        row = conn.execute(
            update(Job)
            .where(Job.status == "queued", Job.id == candidate)
            .values(status="running", worker=worker, attempts=Job.attempts + 1, started_at=now(), heartbeat_at=now())
            .returning(Job.id)
        ).first()
    if row is None:
        return None
    with session_scope() as session:
        job = session.get(Job, row[0])
        return job


def heartbeat(job_id: str) -> None:
    with session_scope() as session:
        session.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=now()))


def finish(job_id: str, error: str | None = None) -> None:
    with session_scope() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "running")
            .values(status="failed" if error else "done", error=error, finished_at=now())
        )


def recover_stale() -> int:
    """Fail jobs whose worker stopped heartbeating, and their runs.

    Paid runs are not retried automatically: a lost worker may have spent money.
    """
    cutoff = now() - STALE_AFTER
    recovered = 0
    with session_scope() as session:
        stale = session.scalars(
            select(Job).where(
                Job.status == "running",
                or_(Job.heartbeat_at < cutoff, (Job.heartbeat_at.is_(None) & (Job.created_at < cutoff))),
            )
        ).all()
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
                from everybench.db import ImportRun

                imp = session.get(ImportRun, job.target_id)
                if imp and imp.status in ("queued", "running"):
                    imp.status = "failed"
                    imp.error = f"Worker {job.worker} stopped responding."
            recovered += 1
    return recovered


def process(job: Job) -> None:
    if job.kind == "run":
        run = execute_run(job.target_id)
        if run.status == "failed":
            raise RuntimeError("Evaluation failed; see sanitized run status.")
    elif job.kind == "import":
        from everybench.imports import execute_import

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
            except Exception:  # noqa: BLE001 - never persist raw provider errors or credentials
                error = "Job execution failed; partial results kept."
                if job.kind == "run":
                    from everybench.execution import fail_run

                    fail_run(job.target_id, error)
                log.error("job %s failed", job.id)
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
