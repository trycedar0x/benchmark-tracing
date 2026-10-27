"""Child-process entry point. Workspace credentials never enter the shared worker environment."""

from __future__ import annotations

import asyncio
import os
import sys

from everybench.auth import secrets_for
from everybench.db import Run, session_scope
from everybench.evaluation.agents import HttpAgent
from everybench.evaluation.engine import EvaluationPersistenceError, evaluate_queued
from everybench.evaluation.submission import EvaluationSubmission
from everybench.evaluation.targets import resolve_target
from everybench.evaluation.traces import HTTPTraceSource, StoreTraceSource
from everybench.execution import fail_run

# These are evaluator client credentials, not executable/configuration environment variables.
PROVIDER_SECRETS = {"OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"}


async def run_evaluation(run_id: str) -> None:
    with session_scope() as session:
        run = session.get(Run, run_id)
    if run is None or run.status != "running":
        return
    try:
        snapshot = run.manifest["evaluation_submission"]
        if snapshot["workspace_id"] != run.workspace_id:
            raise ValueError
        body = EvaluationSubmission.model_validate(snapshot["request"])
        if body.benchmark != run.benchmark or body.target_id != snapshot["target_id"]:
            raise ValueError
        profile = resolve_target(snapshot["target_id"], run.workspace_id)
        if profile.fingerprint() != snapshot["profile_sha256"]:
            raise ValueError
    except Exception:
        fail_run(run_id, "Evaluation target or submission changed or is unavailable; no invocation performed.")
        return
    try:
        credentials = secrets_for(run.workspace_id)

        def credential(name: str) -> str:
            value = credentials.get(name)
            if not value:
                raise ValueError
            return value

        agent = HttpAgent(
            profile.url,
            api_key=credential(profile.token_secret),
            name=profile.name,
            version=profile.version,
            timeout=body.timeout,
        )
        trace = None
        if profile.trace is not None:
            if profile.trace.type == "store":
                trace = StoreTraceSource(workspace_id=run.workspace_id)
            else:
                trace = HTTPTraceSource(
                    profile.trace.url,
                    api_key=credential(profile.trace.token_secret) if profile.trace.token_secret else None,
                    timeout=max(body.trace_timeout, 0.001),
                )
        # This function runs ONLY in the dedicated child process, never in the shared worker.
        for name in PROVIDER_SECRETS:
            os.environ.pop(name, None)
            if name in credentials:
                os.environ[name] = credentials[name]
        for model in [*body.judge_models, *([body.extractor_model] if body.extractor_model else [])]:
            provider = model.partition("/")[0]
            key = {
                "openai": "OPENAI_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
            }.get(provider)
            if key:
                credential(key)
    except Exception:
        fail_run(run_id, "Evaluation scoped credentials unavailable; no invocation performed.")
        return
    try:
        await evaluate_queued(
            run_id,
            body.benchmark,
            agent,
            trace_source=trace,
            workspace_id=run.workspace_id,
            data_path=profile.data_path,
            target_config={"id": profile.id, "version": profile.version},
            **body.model_dump(exclude={"benchmark", "target_id"}),
        )
    except asyncio.CancelledError:
        fail_run(run_id, "Evaluation cancelled; partial results kept.")
    except EvaluationPersistenceError:
        fail_run(run_id, "Evaluation evidence or report persistence failed; partial results kept.")
    except Exception:
        with session_scope() as session:
            phase = session.get(Run, run_id).manifest.get("evaluation_phase", "preflight")
        fail_run(
            run_id,
            "Evaluation preflight failed; no agent invocation performed."
            if phase == "preflight"
            else "Evaluation engine failed; partial results kept.",
        )


if __name__ == "__main__":
    asyncio.run(run_evaluation(sys.argv[1]))
