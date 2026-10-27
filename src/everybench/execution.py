"""Isolated evaluation supervision; no legacy model/sandbox execution dependencies."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable

from sqlalchemy import update

from everybench.config import env, settings
from everybench.db import Run, now, session_scope

TERMINAL = {"succeeded", "failed", "cancelled", "budget_exceeded"}
CANCEL_GRACE_SECONDS = 30


def fail_run(run_id: str, message: str) -> Run:
    """Only fixed diagnostic strings may cross the process boundary."""
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run.status == "failed" and run.error is None:
            run.error = message
        if run.status not in TERMINAL:
            run.status = "cancelled" if run.status == "cancelling" else "failed"
            run.finished_at = now()
            run.error = message
        return run


def _child_env(run_id: str) -> dict[str, str]:
    """No inherited provider credentials, Python hooks, proxy settings or workspace overrides."""
    cfg = settings()
    result = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    result.update({"EVERYBENCH_HOME": str(cfg.home.resolve()), "EVERYBENCH_DATABASE_URL": cfg.database_url})
    for name in ("TARGETS_FILE", "SECRET_KEY"):
        if value := env(name):
            result[f"EVERYBENCH_{name}"] = value
    return result


def execute_run(run_id: str, on_progress: Callable[[Run], None] | None = None, poll: float = 0.1) -> Run:
    with session_scope() as session:
        changed = session.execute(
            update(Run).where(Run.id == run_id, Run.status == "queued").values(status="running", started_at=now())
        ).rowcount
        run = session.get(Run, run_id)
    if not changed:
        return run  # Queued cancellation, completed work and stale jobs never replay.
    if not (run.manifest or {}).get("evaluation_submission"):
        return fail_run(run_id, "Legacy execution is retired; historical results remain readable.")
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "everybench.evaluation.worker", run_id],
            env=_child_env(run_id),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return fail_run(run_id, "Evaluation worker could not start.")
    cancel_at = None
    try:
        while process.poll() is None:
            with session_scope() as session:
                run = session.get(Run, run_id)
            if on_progress:
                on_progress(run)
            if run.status in TERMINAL:
                process.terminate()
                break
            if run.status == "cancelling":
                cancel_at = cancel_at or time.monotonic()
                # Engine polls persisted cancellation and saves partial results before exiting.
                if time.monotonic() - cancel_at > CANCEL_GRACE_SECONDS:
                    process.kill()
                    break
            time.sleep(poll)
        process.wait(timeout=5)
    except Exception:
        process.kill()
        process.wait()
        return fail_run(run_id, "Evaluation worker supervision failed; partial results kept.")
    return fail_run(run_id, "Evaluation worker exited before completion; partial results kept.")


def request_cancel(run_id: str) -> Run:
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise LookupError(f"Run {run_id} not found")
        if run.status == "queued":
            run.status = "cancelled"
            run.finished_at = now()
        elif run.status == "running":
            run.status = "cancelling"
        return run
