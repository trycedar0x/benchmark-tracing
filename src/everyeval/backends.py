"""Execution backends: where a run's process executes.

A backend starts the run process, reports whether it is still running,
delivers cancellation, and exposes its log and artifacts. Results and spans
reach the database from inside the run process, so a backend never handles
evaluation data. Only `local` is implemented; remote backends (Modal, Daytona,
Kubernetes) implement the same five methods and need database access from
where they run.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from everyeval.config import env


@dataclass
class Handle:
    run_id: str
    run_dir: Path
    native: object = field(default=None, repr=False)


class ExecutionBackend(Protocol):
    name: str

    def submit(self, run_id: str, run_dir: Path, env: dict[str, str]) -> Handle: ...

    def status(self, handle: Handle) -> int | None:
        """None while running, otherwise the exit code."""
        ...

    def cancel(self, handle: Handle, force: bool = False) -> None:
        """Graceful stop (the run writes partial results); force kills immediately."""
        ...

    def logs(self, handle: Handle, tail: int = 2000) -> str: ...

    def artifacts(self, handle: Handle) -> list[Path]: ...


class LocalProcessBackend:
    """Runs each run in a child process on this machine. Sandboxed tasks use the local Docker daemon."""

    name = "local"

    def submit(self, run_id: str, run_dir: Path, env: dict[str, str]) -> Handle:
        run_dir.mkdir(parents=True, exist_ok=True)
        log = (run_dir / "child.log").open("ab")
        try:
            proc = subprocess.Popen([sys.executable, "-m", "everyeval.child", run_id], stdout=log, stderr=log, env=env)
        finally:
            log.close()
        return Handle(run_id=run_id, run_dir=run_dir, native=proc)

    def status(self, handle: Handle) -> int | None:
        return handle.native.poll()

    def cancel(self, handle: Handle, force: bool = False) -> None:
        proc: subprocess.Popen = handle.native
        if proc.poll() is not None:
            return
        if force:
            proc.kill()
        else:
            proc.send_signal(signal.SIGINT)

    def logs(self, handle: Handle, tail: int = 2000) -> str:
        path = handle.run_dir / "child.log"
        return path.read_text(errors="replace")[-tail:] if path.exists() else ""

    def artifacts(self, handle: Handle) -> list[Path]:
        return sorted(p for p in handle.run_dir.rglob("*") if p.is_file())


BACKENDS: dict[str, type] = {"local": LocalProcessBackend}


def get_backend(name: str | None = None) -> ExecutionBackend:
    name = name or env("EXECUTION_BACKEND", "local")
    if name not in BACKENDS:
        raise ValueError(f"Unknown execution backend {name!r}; available: {', '.join(BACKENDS)}")
    return BACKENDS[name]()
