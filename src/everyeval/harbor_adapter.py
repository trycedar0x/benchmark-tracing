"""Run agent benchmarks with Harbor and convert trials into results and spans.

Harbor runs an agent on each task in a sandbox (Docker by default) and writes
one directory per trial with `result.json` (rewards, tokens, cost, phase
timings, exceptions) and, for agents that support it, an ATIF trajectory at
`agent/trajectory.json`. Trials are ingested as they finish.

Model strings for Harbor benchmarks:
    terminus-2:openai/gpt-4o   agent terminus-2 driving openai/gpt-4o
    openai/gpt-4o              the catalog's default agent driving openai/gpt-4o
    oracle | nop               reference solution / do nothing (no model, no cost)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

FREE_AGENTS = {"oracle", "nop"}
POLL_SECONDS = 1.0


def split_model(model: str, default_agent: str | None) -> tuple[str, str | None]:
    """Return (agent, model) for a Harbor run."""
    if model in FREE_AGENTS:
        return model, None
    head, sep, rest = model.partition(":")
    if sep and "/" not in head:
        return head, rest or None
    if not default_agent:
        raise ValueError(f"No Harbor agent for {model!r}; use agent:model, e.g. terminus-2:{model}")
    return default_agent, model


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _sid(*parts: Any) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _clip(text: Any, limit: int = 8000) -> Any:
    if not isinstance(text, str):
        return text
    return text if len(text) <= limit else text[:limit] + f"... [truncated {len(text) - limit} chars]"


def trial_reward(result: dict[str, Any]) -> float | None:
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    if not rewards:
        return None
    if "reward" in rewards:
        return float(rewards["reward"])
    return sum(float(v) for v in rewards.values()) / len(rewards)


def _trajectory_spans(
    traj: dict[str, Any], parent: str, trial_id: str, start: datetime | None, end: datetime | None
) -> tuple[list[dict], set[str], str | None, str | None]:
    """ATIF steps -> spans. Returns spans, models seen, first user message, last agent message."""
    steps = traj.get("steps") or []
    spans: list[dict] = []
    models: set[str] = set()
    first_user = last_agent = None
    default_model = (traj.get("agent") or {}).get("model_name")
    n = max(len(steps), 1)
    for i, step in enumerate(steps):
        # Steps without timestamps are spread evenly over the agent phase.
        t0 = _ts(step.get("timestamp"))
        if t0 is None and start and end:
            t0 = start + (end - start) * (i / n)
        t1 = _ts(steps[i + 1].get("timestamp")) if i + 1 < len(steps) else None
        if t1 is None and start and end:
            t1 = start + (end - start) * ((i + 1) / n)
        source = step.get("source")
        message = step.get("message")
        if isinstance(message, list):
            message = "\n".join(p.get("text", "") for p in message if isinstance(p, dict))
        sid = _sid(trial_id, "step", step.get("step_id", i))
        if source == "agent":
            model = step.get("model_name") or default_model
            if model:
                models.add(model)
            metrics = step.get("metrics") or {}
            last_agent = message or last_agent
            spans.append(
                {
                    "span_id": sid,
                    "parent_id": parent,
                    "name": model or "agent step",
                    "kind": "model",
                    "status": "ok",
                    "start_time": t0,
                    "end_time": t1,
                    "attributes": {
                        "atif.step_id": step.get("step_id"),
                        "gen_ai.request.model": model,
                        "gen_ai.usage.input_tokens": metrics.get("prompt_tokens"),
                        "gen_ai.usage.output_tokens": metrics.get("completion_tokens"),
                        "cost_usd": metrics.get("cost_usd"),
                    },
                    "content": {
                        "output": _clip(message),
                        "reasoning": _clip(step.get("reasoning_content")),
                        "tool_calls": [
                            {"function": c.get("function_name"), "arguments": c.get("arguments")}
                            for c in step.get("tool_calls") or []
                        ],
                    },
                }
            )
            results = {r.get("source_call_id"): r for r in ((step.get("observation") or {}).get("results") or [])}
            for call in step.get("tool_calls") or []:
                obs = results.get(call.get("tool_call_id")) or {}
                spans.append(
                    {
                        "span_id": _sid(trial_id, "tool", step.get("step_id", i), call.get("tool_call_id")),
                        "parent_id": sid,
                        "name": call.get("function_name") or "tool",
                        "kind": "tool",
                        "status": "ok",
                        "start_time": t0,
                        "end_time": t1,
                        "attributes": {
                            "tool.name": call.get("function_name"),
                            "tool.call_id": call.get("tool_call_id"),
                        },
                        "content": {"arguments": call.get("arguments"), "result": _clip(obs.get("content"))},
                    }
                )
        else:
            if source == "user" and first_user is None:
                first_user = message
            spans.append(
                {
                    "span_id": sid,
                    "parent_id": parent,
                    "name": f"{source} message",
                    "kind": "event",
                    "status": "ok",
                    "start_time": t0,
                    "end_time": t0,
                    "attributes": {"atif.step_id": step.get("step_id")},
                    "content": {"message": _clip(message)},
                }
            )
    return spans, models, first_user, last_agent


def convert_trial(trial_dir: Path, run_id: str, epoch: int = 1) -> dict[str, Any]:
    """Convert one Harbor trial directory to {"result", "trace", "spans", "resolved_models"}."""
    from everyeval.inspect_convert import trace_id_for

    result = json.loads((trial_dir / "result.json").read_text())
    task = result.get("task_name") or trial_dir.name.split("__")[0]
    trial_id = result.get("id") or trial_dir.name
    trace_id = trace_id_for(run_id, task, epoch)
    reward = trial_reward(result)
    exception = result.get("exception_info")
    agent_result = result.get("agent_result") or {}
    start, end = _ts(result.get("started_at")), _ts(result.get("finished_at"))

    if exception:
        outcome = "error"
    elif reward is None:
        outcome = "unscored"
    else:
        outcome = "correct" if reward >= 1 else "incorrect" if reward <= 0 else "partial"

    root = _sid(trial_id, "root")
    agent_info = result.get("agent_info") or {}
    spans: list[dict] = [
        {
            "span_id": root,
            "parent_id": None,
            "name": f"trial {task}",
            "kind": "agent",
            "status": "error" if exception else "ok",
            "start_time": start,
            "end_time": end,
            "attributes": {
                "harbor.trial": result.get("trial_name"),
                "harbor.agent": agent_info.get("name"),
                "harbor.task_checksum": result.get("task_checksum"),
                "outcome": outcome,
                "score": reward,
                "cost_usd": agent_result.get("cost_usd"),
            },
            "content": None,
        }
    ]
    phases = {}
    for phase in ("environment_setup", "agent_setup", "agent_execution", "verifier"):
        timing = result.get(phase) or {}
        if timing.get("started_at"):
            phases[phase] = _sid(trial_id, phase)
            spans.append(
                {
                    "span_id": phases[phase],
                    "parent_id": root,
                    "name": phase.replace("_", " "),
                    "kind": "scorer" if phase == "verifier" else "span",
                    "status": "ok",
                    "start_time": _ts(timing["started_at"]),
                    "end_time": _ts(timing.get("finished_at")),
                    "attributes": {"score.value": reward} if phase == "verifier" else {},
                    "content": {"rewards": (result.get("verifier_result") or {}).get("rewards")}
                    if phase == "verifier"
                    else None,
                }
            )
    stdout = trial_dir / "verifier" / "test-stdout.txt"
    if "verifier" in phases and stdout.exists():
        spans[-1]["content"] = {**(spans[-1]["content"] or {}), "output": _clip(stdout.read_text(errors="replace"))}

    models: set[str] = set()
    first_user = last_agent = None
    traj_path = trial_dir / "agent" / "trajectory.json"
    if traj_path.exists():
        execution = result.get("agent_execution") or {}
        traj_spans, models, first_user, last_agent = _trajectory_spans(
            json.loads(traj_path.read_text()),
            phases.get("agent_execution", root),
            trial_id,
            _ts(execution.get("started_at")),
            _ts(execution.get("finished_at")),
        )
        spans.extend(traj_spans)
    model_info = agent_info.get("model_info") or {}
    if model_info.get("name") and not models:
        models.add(model_info["name"])
    if exception:
        spans.append(
            {
                "span_id": _sid(trial_id, "exception"),
                "parent_id": root,
                "name": exception.get("exception_type", "error"),
                "kind": "error",
                "status": "error",
                "start_time": _ts(exception.get("occurred_at")) or end,
                "end_time": _ts(exception.get("occurred_at")) or end,
                "attributes": {},
                "content": {
                    "message": _clip(exception.get("exception_message")),
                    "traceback": _clip(exception.get("exception_traceback")),
                },
            }
        )
    for span in spans:
        span["attributes"] = {k: v for k, v in span["attributes"].items() if v is not None}
        if span["end_time"] is None:
            span["end_time"] = span["start_time"]

    instruction = first_user
    task_path = (result.get("task_id") or {}).get("path")
    if instruction is None and task_path:
        candidate = Path(task_path)
        candidate = candidate if candidate.is_absolute() else (trial_dir.parents[2] / candidate)
        if (candidate / "instruction.md").exists():
            instruction = (candidate / "instruction.md").read_text()
    spans[0]["content"] = {"input": _clip(instruction), "output": _clip(last_agent)}

    sample = {
        "sample_id": task,
        "epoch": epoch,
        "outcome": outcome,
        "score": reward,
        "score_raw": json.dumps((result.get("verifier_result") or {}).get("rewards")),
        "scorer": "harbor verifier",
        "answer": _clip(last_agent),
        "explanation": None,
        "target": None,
        "input": _clip(instruction),
        "output": _clip(last_agent),
        "error": None
        if not exception
        else _clip(f"{exception.get('exception_type')}: {exception.get('exception_message')}"),
        "input_tokens": agent_result.get("n_input_tokens") or 0,
        "output_tokens": agent_result.get("n_output_tokens") or 0,
        "total_time": (end - start).total_seconds() if start and end else None,
        "trace_id": trace_id,
    }
    trace = {
        "trace_id": trace_id,
        "sample_id": task,
        "name": f"trial {task}",
        "start_time": start,
        "end_time": end,
        "span_count": len(spans),
        "attributes": {
            "resolved_models": sorted(models),
            "task_checksum": result.get("task_checksum"),
            "cost_usd": agent_result.get("cost_usd"),
        },
    }
    return {
        "result": sample,
        "trace": trace,
        "spans": spans,
        "resolved_models": models,
        "cost_usd": agent_result.get("cost_usd"),
        "task_checksum": result.get("task_checksum"),
    }


def harbor_command(
    task: str, agent: str, model: str | None, jobs_dir: Path, limit: int | None, epochs: int
) -> list[str]:
    exe = shutil.which("harbor", path=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    if exe is None:
        raise RuntimeError("Harbor is not installed. Install it with: uv sync --extra harbor")
    if task.startswith("bundled:"):
        source = ["-p", str(Path(str(resources.files("everyeval") / task.removeprefix("bundled:"))))]
    elif task.startswith(("/", "./")):
        source = ["-p", task]
    else:
        source = ["-d", task]
    cmd = [exe, "run", *source, "-a", agent, "-o", str(jobs_dir), "-y", "-q", "-k", str(epochs)]
    if model:
        cmd += ["-m", model]
    if limit:
        cmd += ["-l", str(limit)]
    return cmd


def run_harbor(
    run_id: str,
    task: str,
    agent: str,
    model: str | None,
    limit: int | None,
    epochs: int,
    budget: float | None,
    run_dir: Path,
    ingest,
    record_cost,
) -> tuple[str, str | None]:
    """Run Harbor, ingesting trials as they finish. Returns (status, error)."""
    jobs_dir = run_dir / "harbor-jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    cmd = harbor_command(task, agent, model, jobs_dir, limit, epochs)
    log = (run_dir / "harbor.log").open("ab")
    proc = subprocess.Popen(cmd, stdout=log, stderr=log, cwd=run_dir, start_new_session=True)
    seen: set[Path] = set()
    attempts: dict[str, int] = {}
    spent = 0.0
    stopped: str | None = None

    def stop(reason: str) -> None:
        nonlocal stopped
        if stopped is None:
            stopped = reason
            os.killpg(proc.pid, signal.SIGINT)

    def collect() -> None:
        nonlocal spent
        for result_file in sorted(jobs_dir.glob("*/*/result.json")):
            trial = result_file.parent
            if trial in seen:
                continue
            try:
                data = json.loads(result_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue  # still being written
            if not data.get("finished_at"):
                continue
            seen.add(trial)
            task_name = data.get("task_name") or trial.name.split("__")[0]
            attempts[task_name] = attempts.get(task_name, 0) + 1
            converted = convert_trial(trial, run_id, attempts[task_name])
            ingest(converted)
            spent += converted.get("cost_usd") or 0.0
            record_cost(spent)
            if budget is not None and spent >= budget:
                stop(f"Budget cap ${budget:.4f} reached (Harbor-reported cost ${spent:.4f}); stopping.")

    deadline_after_stop: float | None = None
    try:
        while proc.poll() is None:
            collect()
            if stopped and deadline_after_stop is None:
                deadline_after_stop = time.monotonic() + 60
            if deadline_after_stop and time.monotonic() > deadline_after_stop:
                os.killpg(proc.pid, signal.SIGKILL)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        stop("cancelled")
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    finally:
        log.close()
    collect()
    if stopped == "cancelled":
        return "cancelled", None
    if stopped:
        return "budget_exceeded", stopped
    if proc.returncode != 0:
        tail = (run_dir / "harbor.log").read_text(errors="replace")[-1500:]
        return "failed", f"Harbor exited with code {proc.returncode}.\n{tail}"
    return "succeeded", None
