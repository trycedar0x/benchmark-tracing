"""Transport-neutral contracts for evaluating user-owned agents.

AgentInput deliberately excludes benchmark references/rubrics. Agent invocation and
trace acquisition are independent capabilities, shared by the CLI and SDK engine.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class AgentInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input: str
    run_id: str
    sample_id: str
    attempt: int = Field(default=1, ge=1)
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    parent_span_id: str = Field(pattern=r"^[0-9a-f]{16}$")

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.parent_span_id}-01"


class AgentResult(BaseModel):
    answer: str = Field(min_length=1)
    trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)


class TraceEvent(BaseModel):
    id: str
    parent_id: str | None = None
    kind: str
    name: str
    status: str = "ok"
    start_time: str | None = None
    end_time: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    content: dict[str, Any] | None = None


class TraceEvidence(BaseModel):
    trace_id: str
    state: Literal["pending", "complete", "incomplete", "missing", "withheld"]
    events: list[TraceEvent] = Field(default_factory=list)
    source: str = "unknown"
    run_id: str | None = None
    sample_id: str | None = None
    attempt: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentTarget(Protocol):
    def describe(self) -> dict[str, Any]: ...

    async def invoke(self, case: AgentInput) -> AgentResult: ...


class TraceSource(Protocol):
    def describe(self) -> dict[str, Any]: ...

    async def get_trace(self, trace_id: str, *, case: AgentInput) -> TraceEvidence: ...


class BenchmarkCase(BaseModel):
    id: str
    input: str
    reference: dict[str, Any] = Field(default_factory=dict, repr=False)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GradeResult(BaseModel):
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    components: dict[str, float | None] = Field(default_factory=dict)
    explanation: str = ""
    protocol: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Benchmark(Protocol):
    name: str
    requires_trace: bool

    def describe(self) -> dict[str, Any]: ...

    def cases(self) -> list[BenchmarkCase]: ...

    async def grade(self, case: BenchmarkCase, result: AgentResult, evidence: TraceEvidence | None) -> GradeResult: ...


class CaseResult(BaseModel):
    sample_id: str
    attempt: int
    status: Literal["scored", "agent_error", "trace_missing", "grading_error", "cancelled", "timeout", "unscored"]
    answer: str | None = None
    score: float | None = None
    components: dict[str, float | None] = Field(default_factory=dict)
    explanation: str | None = None
    trace_id: str | None = None
    trace_state: str | None = None
    error: str | None = None
    duration_seconds: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationReport(BaseModel):
    schema_version: str = "everybench.agent-evaluation.v1"
    run_id: str
    benchmark: str
    target: dict[str, Any]
    status: str
    results: list[CaseResult]
    metrics: dict[str, Any] = Field(default_factory=dict)


AgentCallable = Callable[[AgentInput], AgentResult | str | Awaitable[AgentResult | str]]
TraceCallable = Callable[[str, AgentInput], TraceEvidence | Awaitable[TraceEvidence]]


class AgentInvocationError(RuntimeError):
    """A safe, bounded invocation failure. Do not embed provider bodies or credentials."""


class TraceAccessError(RuntimeError):
    """Trace could not be read/authorized/associated, not an ordinary model zero score."""
