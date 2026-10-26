"""User-owned agent evaluation, independent of Inspect/Harbor execution."""

from everybench.evaluation.agents import CallableAgent, HttpAgent
from everybench.evaluation.benchmarks import FinanceBenchmark
from everybench.evaluation.engine import evaluate, evaluate_sync, report_path
from everybench.evaluation.traces import CallbackTraceSource, HTTPTraceSource, StoreTraceSource
from everybench.evaluation.types import (
    AgentInput,
    AgentInvocationError,
    AgentResult,
    AgentTarget,
    Benchmark,
    BenchmarkCase,
    CaseResult,
    EvaluationReport,
    GradeResult,
    TraceAccessError,
    TraceEvent,
    TraceEvidence,
    TraceSource,
)

__all__ = [
    "AgentInput",
    "AgentInvocationError",
    "AgentResult",
    "AgentTarget",
    "Benchmark",
    "BenchmarkCase",
    "CallableAgent",
    "CallbackTraceSource",
    "CaseResult",
    "EvaluationReport",
    "FinanceBenchmark",
    "GradeResult",
    "HTTPTraceSource",
    "HttpAgent",
    "StoreTraceSource",
    "TraceAccessError",
    "TraceEvent",
    "TraceEvidence",
    "TraceSource",
    "evaluate",
    "evaluate_sync",
    "report_path",
]
