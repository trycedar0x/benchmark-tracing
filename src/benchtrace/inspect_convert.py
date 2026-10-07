"""Convert Inspect AI samples into benchtrace results and spans.

Inspect records a per-sample event stream with explicit span begin/end events.
We map spans to spans, and model/tool/score/sandbox/error events to leaf spans
under the span they occurred in. Unknown event types are kept as opaque spans
so nothing is silently dropped.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from inspect_ai.log import EvalSample
from inspect_ai.scorer import value_to_float

MAX_TEXT = 8000
SKIPPED_EVENTS = {"span_begin", "span_end", "state", "store", "step", "sample_init", "anchor", "timeline"}
SPAN_KINDS = {
    "solver": "solver",
    "solvers": "solver",
    "scorer": "scorer",
    "scorers": "scorer",
    "agent": "agent",
    "tool": "tool",
    "handoff": "agent",
    "init": "span",
    "subtask": "agent",
}

_to_float = value_to_float()


def trace_id_for(run_id: str, sample_id: str, epoch: int) -> str:
    return hashlib.sha256(f"{run_id}|{sample_id}|{epoch}".encode()).hexdigest()[:32]


def _clip(text: str | None) -> str | None:
    if text is None:
        return None
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + f"... [truncated {len(text) - MAX_TEXT} chars]"


def _message(m: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "text": _clip(m.text)}
    calls = getattr(m, "tool_calls", None)
    if calls:
        out["tool_calls"] = [{"function": c.function, "arguments": c.arguments} for c in calls]
    return out


def _ts(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _end(start: datetime | None, completed: Any, working_time: float | None) -> datetime | None:
    start, completed = _ts(start), _ts(completed)
    if completed:
        return completed
    if start and working_time is not None:
        return start + timedelta(seconds=working_time)
    return start


def primary_score(sample: EvalSample) -> tuple[str | None, Any]:
    if not sample.scores:
        return None, None
    name, score = next(iter(sample.scores.items()))
    return name, score


def outcome_for(sample: EvalSample) -> tuple[str, float | None]:
    if sample.error:
        if sample.error.message.startswith("CancelledError"):
            return "cancelled", None
        return "error", None
    _, score = primary_score(sample)
    if score is None:
        return "unscored", None
    try:
        value = _to_float(score.value)
    except Exception:
        return "unscored", None
    if value >= 1:
        return "correct", value
    if value <= 0:
        return "incorrect", value
    return "partial", value


def _input_text(sample: EvalSample) -> str:
    if isinstance(sample.input, str):
        return sample.input
    return "\n".join(f"{m.role}: {m.text}" for m in sample.input)


def _target_text(sample: EvalSample) -> str:
    return sample.target if isinstance(sample.target, str) else " | ".join(sample.target)


def convert_sample(sample: EvalSample, run_id: str) -> dict[str, Any]:
    """Return {"result": ..., "trace": ..., "spans": [...]} with full content.

    Content policy is applied later, at storage time.
    """
    trace_id = trace_id_for(run_id, str(sample.id), sample.epoch)
    outcome, value = outcome_for(sample)
    scorer, score = primary_score(sample)
    usage_in = sum(u.input_tokens for u in sample.model_usage.values())
    usage_out = sum(u.output_tokens for u in sample.model_usage.values())

    result = {
        "sample_id": str(sample.id),
        "epoch": sample.epoch,
        "outcome": outcome,
        "score": value,
        "score_raw": None if score is None else str(score.value),
        "scorer": scorer,
        "answer": None if score is None else _clip(score.answer),
        "explanation": None if score is None else _clip(score.explanation),
        "target": _clip(_target_text(sample)),
        "input": _clip(_input_text(sample)),
        "output": _clip(sample.output.completion if sample.output else None),
        "error": None if not sample.error else _clip(sample.error.message),
        "input_tokens": usage_in,
        "output_tokens": usage_out,
        "total_time": sample.total_time,
        "trace_id": trace_id,
    }

    root_id = f"sample-{trace_id[:16]}"
    started, finished = _ts(sample.started_at), _ts(sample.completed_at)
    spans: dict[str, dict[str, Any]] = {
        root_id: {
            "span_id": root_id,
            "parent_id": None,
            "name": f"sample {sample.id}",
            "kind": "agent",
            "start_time": started,
            "end_time": finished,
            "status": "error" if sample.error else "ok",
            "attributes": {
                "sample.id": str(sample.id),
                "sample.epoch": sample.epoch,
                "outcome": outcome,
                "score": value,
                "input_tokens": usage_in,
                "output_tokens": usage_out,
            },
            "content": {"input": result["input"], "output": result["output"], "target": result["target"]},
        }
    }
    resolved_models: set[str] = set()

    for ev in sample.events:
        kind = ev.event
        if kind == "span_begin":
            spans[ev.id] = {
                "span_id": ev.id,
                "parent_id": ev.parent_id or root_id,
                "name": ev.name,
                "kind": SPAN_KINDS.get(ev.type or "", "span"),
                "start_time": _ts(ev.timestamp),
                "end_time": None,
                "status": "ok",
                "attributes": {"inspect.type": ev.type},
                "content": None,
            }
            continue
        if kind == "span_end":
            if ev.id in spans:
                spans[ev.id]["end_time"] = _ts(ev.timestamp)
            continue
        if kind in SKIPPED_EVENTS:
            continue

        parent = ev.span_id if ev.span_id in spans else root_id
        span: dict[str, Any] = {
            "span_id": ev.uuid or f"{parent}-{len(spans)}",
            "parent_id": parent,
            "start_time": _ts(ev.timestamp),
            "end_time": _ts(ev.timestamp),
            "status": "ok",
            "attributes": {},
            "content": None,
        }
        if kind == "model":
            out = ev.output
            resolved = out.model if out else None
            if resolved and out.choices and not (ev.error or out.error):
                resolved_models.add(resolved)
            usage = out.usage if out else None
            span.update(
                name=ev.model,
                kind="model",
                end_time=_end(ev.timestamp, ev.completed, ev.working_time),
                status="error" if ev.error or (out and out.error) else "ok",
                attributes={
                    "gen_ai.request.model": ev.model,
                    "gen_ai.response.model": resolved,
                    "gen_ai.usage.input_tokens": usage.input_tokens if usage else None,
                    "gen_ai.usage.output_tokens": usage.output_tokens if usage else None,
                    "gen_ai.response.finish_reason": out.stop_reason if out else None,
                    "retries": getattr(ev, "retries", None),
                    "working_time": ev.working_time,
                },
                content={
                    "input": [_message(m) for m in ev.input],
                    "output": _clip(out.completion) if out else None,
                    "tool_calls": [
                        {"function": c.function, "arguments": c.arguments} for c in (out.message.tool_calls or [])
                    ]
                    if out and out.choices
                    else [],
                    "error": ev.error or (out.error if out else None),
                },
            )
        elif kind == "tool":
            # Inspect wraps tool execution in a span of type "tool" (holding any nested
            # sandbox calls); the tool event names its parent. Merge the two into one span.
            wrapper = next(
                (
                    s
                    for s in reversed(spans.values())
                    if s["attributes"].get("inspect.type") == "tool"
                    and s["parent_id"] == parent
                    and s["name"] == ev.function
                    and s["content"] is None
                ),
                None,
            )
            if wrapper is not None:
                span = wrapper
            span.update(
                name=ev.function,
                kind="tool",
                end_time=_end(ev.timestamp, ev.completed, ev.working_time),
                status="error" if ev.error else "ok",
                attributes={
                    **span["attributes"],
                    "tool.name": ev.function,
                    "tool.call_id": ev.id,
                    "working_time": ev.working_time,
                },
                content={
                    "arguments": ev.arguments,
                    "result": _clip(str(ev.result)) if ev.result is not None else None,
                    "error": ev.error.message if ev.error else None,
                },
            )
        elif kind == "score":
            sc = ev.score
            try:
                score_value = _to_float(sc.value)
            except Exception:
                score_value = None
            span.update(
                name=getattr(ev, "scorer", None) or "score",
                kind="scorer",
                attributes={
                    "score.value": score_value,
                    "score.raw": str(sc.value),
                    "score.intermediate": ev.intermediate,
                },
                content={"answer": _clip(sc.answer), "explanation": _clip(sc.explanation), "target": ev.target},
            )
        elif kind == "sandbox":
            span.update(
                name=ev.action,
                kind="sandbox",
                end_time=_end(ev.timestamp, getattr(ev, "completed", None), None),
                status="error" if (ev.result or 0) != 0 else "ok",
                attributes={"sandbox.action": ev.action, "sandbox.result": ev.result},
                content={"cmd": ev.cmd, "input": _clip(ev.input), "output": _clip(ev.output)},
            )
        elif kind == "error":
            span.update(
                name="error",
                kind="error",
                status="error",
                content={"message": _clip(ev.error.message), "traceback": _clip(ev.error.traceback)},
            )
        elif kind == "sample_limit":
            span.update(
                name=f"limit {ev.type}",
                kind="error",
                status="error",
                attributes={"limit.type": ev.type, "limit.value": ev.limit},
                content={"message": ev.message},
            )
        elif kind in ("info", "logger", "approval", "input", "interrupt"):
            span.update(name=kind, kind="event", content={"data": _clip(str(ev.model_dump(exclude={"timestamp"})))})
        else:
            span.update(
                name=kind,
                kind="unknown",
                attributes={"inspect.event": kind},
                content={"data": _clip(str(ev.model_dump()))},
            )
        spans[span["span_id"]] = span

    for span in spans.values():
        if span["end_time"] is None:
            span["end_time"] = span["start_time"]
        span["attributes"] = {k: v for k, v in span["attributes"].items() if v is not None}

    trace = {
        "trace_id": trace_id,
        "sample_id": str(sample.id),
        "name": f"sample {sample.id}",
        "start_time": started,
        "end_time": finished,
        "span_count": len(spans),
        "attributes": {"resolved_models": sorted(resolved_models)},
    }
    return {"result": result, "trace": trace, "spans": list(spans.values()), "resolved_models": resolved_models}
