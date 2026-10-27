"""Validated, atomic queued evaluations; snapshots contain identity, never capabilities."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from everybench.db import Job, Run, session_scope
from everybench.evaluation.benchmarks import FinanceBenchmark
from everybench.evaluation.targets import resolve_target
from everybench.finance.datasets import DATASETS


class EvaluationSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    benchmark: str
    target_id: str = Field(min_length=1, max_length=100)
    judge_models: list[str] = Field(min_length=1, max_length=10)
    extractor_model: str | None = None
    sample_ids: list[str] | None = Field(default=None, min_length=1, max_length=3350)
    limit: int | None = Field(default=None, ge=1, le=3350)
    timeout: float = Field(default=40, gt=0, le=3600)
    trace_timeout: float = Field(default=10, ge=0, le=3600)
    grading_timeout: float = Field(default=180, gt=0, le=3600)
    content_policy: Literal["metadata", "redacted", "full"] = "redacted"
    allow_paid: bool

    @field_validator("allow_paid")
    @classmethod
    def consent(cls, value):
        if value is not True:
            raise ValueError("Explicit allow_paid: true is required; costs are not capped.")
        return value

    @field_validator("sample_ids")
    @classmethod
    def unique_ids(cls, value):
        if value is not None and (len(set(value)) != len(value) or any(not v.strip() or len(v) > 200 for v in value)):
            raise ValueError("Sample IDs must be nonempty and distinct.")
        return value


def submit(body: EvaluationSubmission, workspace_id: str | None) -> Run:
    if body.benchmark not in DATASETS:
        raise LookupError("Evaluation benchmark not found.")
    profile = resolve_target(body.target_id, workspace_id)
    # Constructor checks compatibility and installed evaluator clients, never loads data or invokes a provider.
    benchmark = FinanceBenchmark(body.benchmark, judge_models=body.judge_models, extractor_model=body.extractor_model)
    if benchmark.requires_trace and profile.trace is None:
        raise ValueError("BigFinance requires an authorized trace source.")
    if body.limit is not None and body.limit > DATASETS[body.benchmark].count:
        raise ValueError("Limit exceeds the benchmark case count.")
    with session_scope() as session:
        run = Run(
            workspace_id=workspace_id,
            benchmark=body.benchmark,
            variant_key="pending",
            target_kind="external_agent",
            target={"kind": "external_agent", **profile.public()},
            content_policy=body.content_policy,
            limit=body.limit,
            manifest={
                "evaluation_submission": {
                    "workspace_id": workspace_id,
                    "target_id": profile.id,
                    "profile_sha256": profile.fingerprint(),
                    "request": body.model_dump(),
                }
            },
        )
        session.add(run)
        session.flush()
        session.add(Job(kind="run", target_id=run.id))
    return run
