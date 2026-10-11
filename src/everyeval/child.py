"""Child process that executes one run with Inspect AI.

Started by `everyeval.execution`. Each run gets its own process so a crash,
cancellation or budget stop cannot take down the CLI or a server worker.
Inspect handles SIGINT as a graceful cancel and writes a partial log, which
is how both budget stops and user cancellation end a run.

Usage: python -m everyeval.child RUN_ID
"""

from __future__ import annotations

import os
import platform
import signal
import sys
import traceback
from importlib import metadata
from pathlib import Path
from typing import Any

from inspect_ai import eval as inspect_eval
from inspect_ai.hooks import Hooks, ModelUsageData, SampleEnd, TaskStart, hooks
from inspect_ai.log import EvalLog, list_eval_logs, read_eval_log

from everyeval.catalog import get_benchmark
from everyeval.config import settings
from everyeval.db import Run, SampleResult, now, session_scope
from everyeval.ingest import store_sample
from everyeval.inspect_convert import convert_sample
from everyeval.pricing import price_for


class _State:
    run_id: str | None = None
    model: str | None = None
    budget: float | None = None
    spent: float = 0.0
    budget_stop: bool = False


STATE = _State()
BUDGET_MAX_SAMPLES = 4


@hooks(name="everyeval", description="Streams results and traces into everyeval and enforces budget caps.")
class EveryEvalHooks(Hooks):
    def enabled(self) -> bool:
        return STATE.run_id is not None

    async def on_task_start(self, data: TaskStart) -> None:
        dataset = data.spec.dataset
        total = dataset.samples or 0
        limit = data.spec.config.limit
        if isinstance(limit, int):
            total = min(total, limit)
        elif isinstance(limit, tuple | list):
            total = min(total, limit[1] - limit[0])
        epochs = data.spec.config.epochs or 1
        with session_scope() as session:
            run = session.get(Run, STATE.run_id)
            run.samples_total = total * epochs

    async def on_sample_end(self, data: SampleEnd) -> None:
        converted = convert_sample(data.sample, STATE.run_id)
        with session_scope() as session:
            store_sample(session, session.get(Run, STATE.run_id), converted)

    async def on_model_usage(self, data: ModelUsageData) -> None:
        if STATE.budget is None or STATE.budget_stop:
            return
        price = price_for(STATE.model)
        if price is None:
            return
        STATE.spent += price.cost(data.usage.input_tokens, data.usage.output_tokens)
        if STATE.spent >= STATE.budget:
            STATE.budget_stop = True
            with session_scope() as session:
                run = session.get(Run, STATE.run_id)
                run.error = f"Budget cap ${STATE.budget:.4f} reached (estimated ${STATE.spent:.4f}); stopping."
            os.kill(os.getpid(), signal.SIGINT)


def _versions() -> dict[str, str]:
    out = {"python": platform.python_version()}
    for pkg in ("everyeval", "inspect_ai", "inspect_evals"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    return out


def _metrics(log: EvalLog) -> dict[str, Any]:
    if not log.results:
        return {}
    return {score.name: {name: m.value for name, m in score.metrics.items()} for score in log.results.scores}


def _latest_log(log_dir: Path) -> EvalLog | None:
    logs = list_eval_logs(str(log_dir))
    return read_eval_log(logs[0]) if logs else None


def _reconcile(run_id: str, log: EvalLog) -> None:
    """Ingest any samples the live hook missed, then record final metrics."""
    with session_scope() as session:
        run = session.get(Run, run_id)
        stored = {(s.sample_id, s.epoch) for s in session.query(SampleResult).filter_by(run_id=run_id)}
        for sample in log.samples or []:
            if (str(sample.id), sample.epoch) not in stored:
                store_sample(session, run, convert_sample(sample, run_id))
        run.metrics = _metrics(log)
        run.log_path = log.location
        run.manifest = {
            **(run.manifest or {}),
            "inspect": {
                "task": log.eval.task,
                "task_version": log.eval.task_version,
                "task_args": log.eval.task_args,
                "model": log.eval.model,
                "model_generate_config": log.eval.model_generate_config.model_dump(exclude_none=True),
                "packages": log.eval.packages,
                "dataset": log.eval.dataset.model_dump(exclude_none=True),
                "scorers": [s.name for s in (log.results.scores if log.results else [])],
            },
        }


def run_child(run_id: str) -> int:
    cfg = settings()
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            print(f"Run {run_id} not found", file=sys.stderr)
            return 2
        entry = get_benchmark(run.benchmark)
        run.status = "running"
        run.started_at = now()
        run.manifest = {**(run.manifest or {}), "versions": _versions(), "platform": platform.platform()}
        model, limit, epochs, budget = run.model, run.limit, run.epochs, run.budget_usd
        custom_agent, agent_path = run.agent, (run.manifest or {}).get("agent_path")

    STATE.run_id, STATE.model, STATE.budget = run_id, model, budget
    if entry.adapter == "harbor":
        return _run_harbor_child(
            run_id, entry, model, limit, epochs, budget, cfg.logs_dir / run_id, custom_agent, agent_path
        )
    log_dir = cfg.logs_dir / run_id
    status, error = "failed", None
    log: EvalLog | None = None
    try:
        logs = inspect_eval(
            entry.task,
            task_args=entry.task_args,
            model=model,
            limit=limit,
            epochs=epochs,
            log_dir=str(log_dir),
            display="none",
            fail_on_error=False,
            retry_on_error=0,
            log_realtime=True,
            metadata={"everyeval_run_id": run_id},
            # With a budget, bound in-flight work so a stop overshoots by at most a few calls.
            max_samples=BUDGET_MAX_SAMPLES if budget is not None else None,
        )
        log = logs[0] if logs else _latest_log(log_dir)
    except KeyboardInterrupt:
        log = _latest_log(log_dir)
    except Exception as ex:  # noqa: BLE001 - record any failure on the run
        error = f"{type(ex).__name__}: {ex}\n{traceback.format_exc(limit=5)}"
        log = _latest_log(log_dir)

    if log is not None:
        if log.status == "success":
            status = "succeeded"
        elif log.status == "cancelled" or STATE.budget_stop:
            status = "cancelled"
        else:
            status = "failed"
            error = error or (log.error.message if log.error else "Run failed")
        if log.samples is None or not log.samples:
            try:
                log = read_eval_log(log.location)
            except Exception:  # noqa: BLE001
                pass
        _reconcile(run_id, log)

    with session_scope() as session:
        run = session.get(Run, run_id)
        if STATE.budget_stop:
            status = "budget_exceeded"
        elif run.status == "cancelling":
            status = "cancelled"
        run.status = status
        run.finished_at = now()
        if error:
            run.error = error
    return 0 if status == "succeeded" else 1


def _run_harbor_child(run_id, entry, model, limit, epochs, budget, run_dir, custom_agent=None, agent_path=None) -> int:
    from everyeval.harbor_adapter import run_harbor, split_model

    checksums: dict[str, str] = {}
    revisions: dict[str, str] = {}

    def ingest(converted: dict[str, Any]) -> None:
        if converted.get("task_checksum"):
            checksums[converted["result"]["sample_id"]] = converted["task_checksum"]
        if converted.get("benchmark_revision"):
            revisions[converted["result"]["sample_id"]] = converted["benchmark_revision"]
        with session_scope() as session:
            store_sample(session, session.get(Run, run_id), converted)

    def record_cost(spent: float) -> None:
        with session_scope() as session:
            run = session.get(Run, run_id)
            if spent:
                run.cost_usd = spent

    agent = None
    try:
        agent, agent_model = (custom_agent, model) if custom_agent else split_model(model, entry.agent)
        task = entry.task
        if entry.patches:
            from everyeval.config import settings
            from everyeval.harbor_adapter import patched_dataset_dir, prepare_patched_dataset

            dest = patched_dataset_dir(settings().home / "harbor-datasets", entry.task, entry.patches)
            task = str(prepare_patched_dataset(entry.task, entry.patches, dest))
        status, error = run_harbor(
            run_id,
            task,
            agent,
            agent_model,
            limit,
            epochs,
            budget,
            run_dir,
            ingest,
            record_cost,
            task_filter=entry.task_filter,
            env=entry.env,
            agent_path=agent_path,
            env_defaults=entry.env_defaults,
        )
    except Exception as ex:  # noqa: BLE001 - record any failure on the run
        status, error = "failed", f"{type(ex).__name__}: {ex}"
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run.status == "cancelling":
            status = "cancelled"
        run.status = status
        run.finished_at = now()
        run.log_path = str(run_dir / "harbor-jobs")
        run.manifest = {
            **(run.manifest or {}),
            "harbor": {"agent": agent, "task_checksums": checksums, "benchmark_revisions": revisions},
        }
        if error:
            run.error = error
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(run_child(sys.argv[1]))
