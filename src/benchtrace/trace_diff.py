"""Diff two traces of the same task: where did the trajectories diverge?

Each trace is reduced to its steps (model, tool, scorer, sandbox and error
spans) in time order. Steps are aligned on their kind and name, then aligned
pairs are compared on what they produced. The first step that differs is the
divergence point.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from benchtrace.db import SampleResult, Span, Trace

STEP_KINDS = {"model", "tool", "scorer", "sandbox", "error"}


@dataclass
class Step:
    span_id: str
    kind: str
    name: str
    status: str
    summary: str
    output: Any


@dataclass
class StepPair:
    op: str  # same | changed | only_a | only_b
    a: Step | None
    b: Step | None
    differences: list[str] = field(default_factory=list)


@dataclass
class TraceDiff:
    a: dict[str, Any]
    b: dict[str, Any]
    pairs: list[StepPair]
    first_divergence: int | None
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _output(span: Span) -> Any:
    """The part of a step that defines what it produced, for comparison."""
    content = span.content or {}
    attrs = span.attributes or {}
    if span.kind == "model":
        return {
            "output": content.get("output"),
            "tool_calls": content.get("tool_calls") or [],
            "error": content.get("error"),
        }
    if span.kind == "tool":
        return {"arguments": content.get("arguments"), "result": content.get("result"), "error": content.get("error")}
    if span.kind == "scorer":
        return {"score": attrs.get("score.value"), "answer": content.get("answer")}
    if span.kind == "sandbox":
        return {"cmd": content.get("cmd"), "output": content.get("output"), "result": attrs.get("sandbox.result")}
    return {"message": content.get("message")}


def _summary(span: Span) -> str:
    content = span.content or {}
    attrs = span.attributes or {}
    if span.kind == "model":
        calls = content.get("tool_calls") or []
        if calls:
            return "calls " + ", ".join(f"{c['function']}({_short(c.get('arguments'))})" for c in calls)
        return _short(content.get("output") or content.get("error") or "")
    if span.kind == "tool":
        return f"{_short(content.get('arguments'))} → {_short(content.get('result') or content.get('error'))}"
    if span.kind == "scorer":
        return f"score {attrs.get('score.value')} · answer {_short(content.get('answer'))}"
    if span.kind == "sandbox":
        return _short(content.get("cmd"))
    return _short(content.get("message") or span.name)


def _short(value: Any, limit: int = 120) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def steps_for(session: Session, trace_id: str) -> list[Step]:
    spans = session.scalars(
        select(Span).where(Span.trace_id == trace_id, Span.kind.in_(STEP_KINDS)).order_by(Span.start_time, Span.id)
    ).all()
    return [
        Step(span_id=s.span_id, kind=s.kind, name=s.name, status=s.status, summary=_summary(s), output=_output(s))
        for s in spans
    ]


def _differences(a: Step, b: Step) -> list[str]:
    diffs = []
    if a.status != b.status:
        diffs.append(f"status {a.status} → {b.status}")
    if a.output != b.output:
        for key in sorted(set(a.output) | set(b.output)):
            if a.output.get(key) != b.output.get(key):
                diffs.append(key)
    return diffs


def diff_steps(a: list[Step], b: list[Step]) -> tuple[list[StepPair], int | None]:
    def key(step: Step) -> tuple[str, str]:
        # Models are matched by kind only, since mock and real model names differ across runs.
        return (step.kind, "" if step.kind == "model" else step.name)

    matcher = SequenceMatcher(a=[key(s) for s in a], b=[key(s) for s in b], autojunk=False)
    pairs: list[StepPair] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for x, y in zip(a[i1:i2], b[j1:j2], strict=True):
                diffs = _differences(x, y)
                pairs.append(StepPair("changed" if diffs else "same", x, y, diffs))
        else:
            left, right = a[i1:i2], b[j1:j2]
            for n in range(max(len(left), len(right))):
                x = left[n] if n < len(left) else None
                y = right[n] if n < len(right) else None
                if x and y and key(x) == key(y):
                    diffs = _differences(x, y)
                    pairs.append(StepPair("changed" if diffs else "same", x, y, diffs))
                elif x and y:
                    pairs.append(StepPair("only_a", x, None))
                    pairs.append(StepPair("only_b", None, y))
                elif x:
                    pairs.append(StepPair("only_a", x, None))
                else:
                    pairs.append(StepPair("only_b", None, y))
    first = next((i for i, p in enumerate(pairs) if p.op != "same"), None)
    return pairs, first


def _header(session: Session, trace_id: str) -> dict[str, Any]:
    trace = session.get(Trace, trace_id)
    if trace is None:
        raise LookupError(f"Trace {trace_id} not found")
    sample = session.scalars(select(SampleResult).where(SampleResult.trace_id == trace_id)).first()
    return {
        "trace_id": trace_id,
        "run_id": trace.run_id,
        "name": trace.name,
        "source": trace.source,
        "workspace_id": trace.workspace_id,
        "outcome": sample.outcome if sample else None,
        "score": sample.score if sample else None,
    }


def diff_traces(session: Session, trace_a: str, trace_b: str) -> TraceDiff:
    a_header, b_header = _header(session, trace_a), _header(session, trace_b)
    pairs, first = diff_steps(steps_for(session, trace_a), steps_for(session, trace_b))
    if first is None:
        summary = "The two traces took the same steps with the same results."
    else:
        p = pairs[first]
        step = p.a or p.b
        what = {"only_a": "only in A", "only_b": "only in B"}.get(p.op, "differs: " + ", ".join(p.differences))
        summary = f"Diverged at step {first + 1} ({step.kind} {step.name}): {what}."
    return TraceDiff(a=a_header, b=b_header, pairs=pairs, first_divergence=first, summary=summary)
