"""Render only captured, visible model/tool content for neutral trace grading."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from everybench.evaluation.types import TraceAccessError, TraceEvent, TraceEvidence
from everybench.otlp import span_kind

TRACE_CAP = 150_000
TOOL_RESULT_CAP = 4000
TOOL_ARGUMENT_CAP = 1500

_EXCLUDED = {"scorer", "evaluator", "judge"}
_HIDDEN_FIELDS = {
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "analysis",
    "thinking",
    "signature",
    "encrypted_content",
}
_HIDDEN_BLOCKS = {"reasoning", "reasoning_text", "analysis", "thinking", "redacted_thinking"}
_VISIBLE_BLOCKS = {
    "message",
    "text",
    "input_text",
    "output_text",
    "function_call",
    "tool_call",
    "tool_call_response",
    "tool_result",
}


def normalize_event(event: TraceEvent) -> TraceEvent:
    """Semantic aliases must not override an explicit evaluator classification."""
    original = event.kind.lower()
    classifications = [
        event.attributes.get(key)
        for key in ("everybench.span.kind", "everyeval.span.kind", "benchtrace.span.kind", "openinference.span.kind")
    ]
    if original in _EXCLUDED or any(str(value).lower() in _EXCLUDED for value in classifications):
        kind = "scorer"
    else:
        kind = span_kind(event.attributes, event.name).lower()
        if kind == "span":
            kind = original
    return event.model_copy(update={"kind": kind, "attributes": dict(event.attributes)})


def capture_withheld(event: TraceEvent) -> bool:
    """A delivery seal does not certify disclosure of producer-omitted bodies."""
    return event.attributes.get("everybench.evidence.withheld") is True


def _messages(value: Any) -> Any:
    """Remove reasoning/analysis parts rather than dumping raw model payloads."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    if not isinstance(value, list):
        return None
    result = []
    for message in value:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant", "system", "tool"):
            continue
        if message.get("channel") in ("analysis", "reasoning"):
            continue
        visible: dict[str, Any] = {"role": message["role"]}
        parts = message.get("parts", message.get("content"))
        if isinstance(parts, str):
            visible["content"] = parts
        elif isinstance(parts, list):
            kept = []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                kind = part.get("type")
                if kind in ("text", "input_text", "output_text"):
                    text = part.get("text", part.get("content"))
                    if isinstance(text, str) and text:
                        kept.append({"type": "text", "text": text})
                elif kind in ("tool_call", "tool_call_response", "tool_result"):
                    kept.append(
                        {
                            key: _model_value(part[key])
                            for key in ("type", "id", "name", "arguments", "result", "response")
                            if key in part
                        }
                    )
            if kept:
                visible["parts"] = kept
        # Chat-completions style tool calls are visible actions, not reasoning.
        calls = []
        for key in ("tool_calls", "toolCalls"):
            for call in message.get(key, []) if isinstance(message.get(key), list) else []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if isinstance(function, dict):
                    name, arguments = function.get("name"), function.get("arguments")
                elif key == "toolCalls":
                    name, arguments = call.get("name"), call.get("input")
                else:
                    continue
                calls.append({"id": call.get("id"), "name": name, "arguments": _model_value(arguments)})
        if calls:
            visible["tool_calls"] = calls
        if len(visible) > 1:
            result.append(visible)
    return result or None


def _model_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, list | dict):
            return _model_value(parsed)
        return value
    if isinstance(value, list):
        # Recurse per item: mixed/nested provider arrays may also contain domain data.
        result = []
        for item in value:
            visible = _model_value(item)
            if visible is not None or item is None:
                if isinstance(item, dict) and "role" in item and isinstance(visible, list):
                    result.extend(visible)
                else:
                    result.append(visible)
        return result if result or not value else None
    if isinstance(value, dict):
        if str(value.get("channel")) in _HIDDEN_BLOCKS or str(value.get("type")) in _HIDDEN_BLOCKS:
            return None
        if "role" in value:
            return _messages([value])
        if "messages" in value:
            return _messages(value["messages"])
        if "choices" in value:
            choices = value["choices"]
            if not isinstance(choices, list):
                return None
            return _model_value(
                [
                    choice.get("message", choice.get("delta", choice.get("text")))
                    for choice in choices
                    if isinstance(choice, dict)
                ]
            )
        output = value.get("output")
        if isinstance(output, list) and (
            value.get("object") == "response"
            or any(
                isinstance(item, dict) and str(item.get("type")) in _VISIBLE_BLOCKS | _HIDDEN_BLOCKS for item in output
            )
        ):
            # Responses API: unsupported provider blocks are not domain answers.
            return (
                _model_value(
                    [item for item in output if isinstance(item, dict) and str(item.get("type")) in _VISIBLE_BLOCKS]
                )
                or None
            )
        if value.get("type") in ("text", "input_text", "output_text"):
            return {key: _model_value(value[key]) for key in ("type", "text", "content") if key in value}
        if any(key in value for key in _HIDDEN_FIELDS) and any(
            key in value for key in ("text", "content", "output_text")
        ):
            # A recognized text/reasoning envelope is not arbitrary domain JSON.
            return {
                key: visible
                for key in ("text", "content", "output_text")
                if key in value and (visible := _model_value(value[key])) is not None
            } or None
        if value.get("type") in ("function_call", "tool_call", "tool_call_response", "tool_result"):
            return {
                key: _model_value(item)
                for key, item in value.items()
                if key in ("type", "id", "call_id", "name", "arguments", "result", "response")
            }
        # Unsupported provider envelopes are not visible answers. Domain JSON is
        # retained recursively, including financial arrays and nested result data.
        if value.get("object") in ("chat.completion", "chat.completion.chunk", "response"):
            return {key: _model_value(value[key]) for key in ("text", "content", "output_text") if key in value} or None
        result = {
            key: visible
            for key, item in value.items()
            if key not in _HIDDEN_FIELDS and ((visible := _model_value(item)) is not None or item is None)
        }
        return result if result or not value else None
    return value


def visible_content(event: TraceEvent) -> dict[str, Any]:
    """Whitelist interoperable visible-body keys; ignore attributes and reasoning."""
    if event.attributes.get("everybench.content_state", "present") != "present":
        return {}
    content = dict(event.content or {})
    # EveryBench.record uses these explicit native SDK keys. Canonicalize only
    # visible body aliases, never arbitrary records (reasoning/private fields).
    for alias, canonical in (
        ("input", "input.value"),
        ("output", "output.value"),
        ("tool_arguments", "tool.arguments"),
        ("tool_result", "tool.result"),
    ):
        sdk_key = f"everybench.content.{alias}"
        if canonical not in content and sdk_key in content:
            content[canonical] = content[sdk_key]
    visible: dict[str, Any] = {}
    if event.kind == "model":
        for key in ("gen_ai.input.messages", "gen_ai.output.messages"):
            if key in content:
                value = _messages(content[key])
                if value:
                    visible[key] = value
        for key in ("input.value", "output.value"):
            if key in content:
                value = _model_value(content[key])
                if value:
                    visible[key] = value
    elif event.kind == "tool":
        for key in (
            "gen_ai.tool.call.arguments",
            "gen_ai.tool.call.result",
            "input.value",
            "output.value",
            "tool.arguments",
            "tool.result",
        ):
            if key in content and content[key] is not None:
                visible[key] = content[key]
    return visible


def _dump(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _cap(text: str, limit: int, label: str) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    digest = hashlib.sha256(text.encode()).hexdigest()
    marker = f"\n... [{label} truncated; chars={len(text)} sha256={digest}] ...\n"
    room = max(0, limit - len(marker))
    return text[: room // 2] + marker + text[-(room - room // 2) :], True


def _cap_model_calls(value: Any) -> tuple[Any, bool]:
    truncated = False
    if isinstance(value, list):
        result = []
        for item in value:
            bounded, cut = _cap_model_calls(item)
            result.append(bounded)
            truncated |= cut
        return result, truncated
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in ("arguments", "result", "response"):
                bounded, cut = _cap(_dump(item), TOOL_ARGUMENT_CAP if key == "arguments" else TOOL_RESULT_CAP, key)
                result[key] = bounded if cut else item
            else:
                result[key], cut = _cap_model_calls(item)
            truncated |= cut
        return result, truncated
    return value, False


def _ordered(events: list[TraceEvent]) -> list[TraceEvent]:
    def key(pair: tuple[int, TraceEvent]) -> tuple[float, int]:
        index, event = pair
        try:
            when = datetime.fromisoformat(event.start_time) if event.start_time else None
        except ValueError:
            raise TraceAccessError("Trace has malformed span timestamps.") from None
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return (when.timestamp() if when else float("-inf"), index)

    return [event for _, event in sorted(enumerate(events), key=key)]


def format_evidence(evidence: TraceEvidence) -> tuple[str, bool]:
    """Return ordered captured events, never synthetic assistant turns or answers.

    Source adapters validate ownership and completeness first. Metadata-only or
    incomplete evidence is not a full execution trace and must not reach a judge.
    """
    if evidence.state != "complete":
        raise TraceAccessError("Trace-aware grading requires complete visible evidence.")
    events = []
    by_id: dict[str, TraceEvent] = {}
    for event in evidence.events:
        if not event.id or event.id in by_id:
            raise TraceAccessError("Trace has duplicate or malformed events.")
        event = normalize_event(event)
        by_id[event.id] = event
        events.append(event)
    excluded = {
        event.id
        for event in events
        if event.kind in ("scorer", "evaluator", "judge")
        or event.attributes.get("everybench.role") in ("scorer", "evaluator", "judge")
        or event.attributes.get("everybench.phase") in ("grading", "evaluation")
    }
    for event in events:
        seen = {event.id}
        parent = event.parent_id
        while parent in by_id:
            if parent in seen:
                raise TraceAccessError("Trace contains a parent cycle.")
            seen.add(parent)
            if parent in excluded:
                excluded.add(event.id)
            parent = by_id[parent].parent_id
    lines: list[str] = []
    truncated = False
    for event in _ordered(events):
        if event.id in excluded:
            continue
        if capture_withheld(event):
            raise TraceAccessError("Trace contains producer-withheld evidence.")
        if event.kind not in ("model", "tool"):
            continue
        content = visible_content(event)
        if not content:
            raise TraceAccessError("Model/tool evidence has no visible content.")
        identity = {
            key: event.attributes[key]
            for key in (
                "gen_ai.request.model",
                "gen_ai.response.model",
                "gen_ai.provider.name",
                "llm.model_name",
                "gen_ai.tool.name",
                "gen_ai.tool.call.id",
                "tool.name",
                "tool_call_id",
            )
            if key in event.attributes
        }
        lines.append(f"{event.kind} {event.name} id={event.id} parent={event.parent_id} status={event.status}")
        if identity:
            lines.append("identity: " + _dump(identity))
        for key, value in content.items():
            if event.kind == "model":
                value, cut = _cap_model_calls(value)
                truncated |= cut
            text = _dump(value)
            limit = TRACE_CAP
            if event.kind == "tool":
                limit = (
                    TOOL_ARGUMENT_CAP
                    if key in ("gen_ai.tool.call.arguments", "input.value", "tool.arguments")
                    else TOOL_RESULT_CAP
                )
            text, cut = _cap(text, limit, key)
            truncated |= cut
            lines.append(f"{key}: {text}")
    if not lines:
        raise TraceAccessError("Trace-aware grading requires actual visible model/tool events.")
    trace, cut = _cap("\n".join(lines), TRACE_CAP, "trace middle")
    return trace, truncated or cut
