"""Run supervision: start a run's child process, relay cancellation, detect crashes."""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections.abc import Callable

from benchtrace.config import settings
from benchtrace.db import Run, now, session_scope

TERMINAL = {"succeeded", "failed", "cancelled", "budget_exceeded"}
CANCEL_GRACE_SECONDS = 30


def execute_run(run_id: str, on_progress: Callable[[Run], None] | None = None, poll: float = 0.5) -> Run:
    cfg = settings()
    run_dir = cfg.logs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    child_log = run_dir / "child.log"
    with child_log.open("ab") as out:
        proc = subprocess.Popen([sys.executable, "-m", "benchtrace.child", run_id], stdout=out, stderr=out)
        cancel_sent_at: float | None = None
        while True:
            exited = proc.poll() is not None
            with session_scope() as session:
                run = session.get(Run, run_id)
            if on_progress:
                on_progress(run)
            if exited:
                break
            if run.status == "cancelling":
                if cancel_sent_at is None:
                    proc.send_signal(signal.SIGINT)
                    cancel_sent_at = time.monotonic()
                elif time.monotonic() - cancel_sent_at > CANCEL_GRACE_SECONDS:
                    proc.kill()
            time.sleep(poll)

    with session_scope() as session:
        run = session.get(Run, run_id)
        if run.status not in TERMINAL:
            tail = child_log.read_text(errors="replace")[-2000:]
            run.status = "failed"
            run.finished_at = now()
            run.error = f"Run process exited with code {proc.returncode}.\n{tail}"
    return run


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
