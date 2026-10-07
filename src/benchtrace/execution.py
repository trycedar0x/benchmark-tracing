"""Run supervision: start a run's child process, relay cancellation, detect crashes."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from benchtrace.backends import get_backend
from benchtrace.config import settings
from benchtrace.db import Run, now, session_scope

TERMINAL = {"succeeded", "failed", "cancelled", "budget_exceeded"}
CANCEL_GRACE_SECONDS = 30


def execute_run(run_id: str, on_progress: Callable[[Run], None] | None = None, poll: float = 0.5) -> Run:
    backend = get_backend()
    handle = backend.submit(run_id, settings().logs_dir / run_id, _child_env(run_id))
    with session_scope() as session:
        run = session.get(Run, run_id)
        run.manifest = {**(run.manifest or {}), "execution_backend": backend.name}
    cancel_sent_at: float | None = None
    while True:
        exited = backend.status(handle) is not None
        with session_scope() as session:
            run = session.get(Run, run_id)
        if on_progress:
            on_progress(run)
        if exited:
            break
        if run.status == "cancelling":
            if cancel_sent_at is None:
                backend.cancel(handle)
                cancel_sent_at = time.monotonic()
            elif time.monotonic() - cancel_sent_at > CANCEL_GRACE_SECONDS:
                backend.cancel(handle, force=True)
        time.sleep(poll)

    with session_scope() as session:
        run = session.get(Run, run_id)
        if run.status not in TERMINAL:
            run.status = "failed"
            run.finished_at = now()
            run.error = f"Run process exited with code {backend.status(handle)}.\n{backend.logs(handle)}"
    return run


def _child_env(run_id: str) -> dict[str, str]:
    """The run's process sees its own workspace's secrets (provider keys) on top of the server environment."""
    from benchtrace.auth import secrets_for

    with session_scope() as session:
        workspace_id = session.get(Run, run_id).workspace_id
    return {**os.environ, **secrets_for(workspace_id)}


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
