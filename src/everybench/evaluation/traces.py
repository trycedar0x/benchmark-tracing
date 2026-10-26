"""Authorized trace adapters, with fail-closed invocation/root association.

An external callback is an explicit SDK capability. Its author is responsible for
platform/project authorization and a real platform completion check, never a
final-answer/end-timestamp heuristic. Store/HTTP use EveryBench's producer seal.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime
from typing import Any

import httpx
from pydantic import PrivateAttr
from sqlalchemy import select

from everybench.db import Span, Trace, session_scope
from everybench.evaluation.agents import (
    _authorization,
    _endpoint_description,
    _strict_json,
    _timeout,
    _validate_url,
)
from everybench.evaluation.evidence import capture_withheld, normalize_event, visible_content
from everybench.evaluation.types import AgentInput, TraceAccessError, TraceCallable, TraceEvent, TraceEvidence

_BINDINGS = {"everybench.run_id": "run_id", "everybench.sample_id": "sample_id", "everybench.attempt": "attempt"}
_EXCLUDED = {"scorer", "evaluator", "judge"}


def _request_id(trace_id: str, case: AgentInput) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", trace_id) or trace_id == "0" * 32 or trace_id != case.trace_id:
        raise TraceAccessError("Trace ID is not bound to this invocation.")


def _missing(trace_id: str, source: str) -> TraceEvidence:
    return TraceEvidence(trace_id=trace_id, state="missing", source=source)


class _ValidatedEvidence(TraceEvidence):
    # In-process only: excluded raw spans still count toward producer delivery.
    # Never accept a serialized metadata flag as proof of their receipt.
    _excluded_events: list[TraceEvent] = PrivateAttr(default_factory=list)


def _validate_evidence(evidence: TraceEvidence, case: AgentInput, *, trusted_callback: bool) -> TraceEvidence:
    _request_id(evidence.trace_id, case)
    for field in ("run_id", "sample_id", "attempt"):
        value = getattr(evidence, field)
        if value is not None and (value != getattr(case, field) or (field == "attempt" and type(value) is not int)):
            raise TraceAccessError("Trace header belongs to another invocation.")
    if evidence.state in ("missing", "pending") and not evidence.events:
        return evidence
    raw_events = list(evidence.events)
    if isinstance(evidence, _ValidatedEvidence):
        raw_events.extend(evidence._excluded_events)
    events = [normalize_event(event) for event in raw_events]
    by_id: dict[str, TraceEvent] = {}
    for event in events:
        if not event.id or event.id in by_id or event.id == event.parent_id or not event.name:
            raise TraceAccessError("Trace has malformed or duplicate spans.")
        for timestamp in (event.start_time, event.end_time):
            if timestamp is not None:
                try:
                    datetime.fromisoformat(timestamp)
                except ValueError:
                    raise TraceAccessError("Trace has malformed span timestamps.") from None
        for key, field in _BINDINGS.items():
            value = event.attributes.get(key)
            if value is not None and (value != getattr(case, field) or (field == "attempt" and type(value) is not int)):
                raise TraceAccessError("Trace span belongs to another invocation.")
        if event.attributes.get("trace_id", evidence.trace_id) != case.trace_id:
            raise TraceAccessError("Trace span has a mismatched trace ID.")
        by_id[event.id] = event
    excluded = {
        event.id
        for event in events
        if event.kind in _EXCLUDED
        or event.attributes.get("everybench.role") in _EXCLUDED
        or event.attributes.get("everybench.phase") in ("evaluation", "grading")
    }
    # A model under a scorer is evaluator traffic too, not user-agent evidence.
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
    included = [event for event in events if event.id not in excluded]
    roots = [event for event in included if event.parent_id not in by_id]
    if len(roots) != 1 or roots[0].kind != "agent" or roots[0].parent_id not in (None, case.parent_span_id):
        raise TraceAccessError("Trace requires one actual invocation agent root.")
    root = roots[0]
    for key, field in _BINDINGS.items():
        value = root.attributes.get(key)
        if value != getattr(case, field) or (field == "attempt" and type(value) is not int):
            raise TraceAccessError("Agent root is missing immutable invocation binding.")
    expected = evidence.metadata.get("expected_spans")
    sealed = evidence.metadata.get("sealed_at")
    if expected is not None:
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
            raise TraceAccessError("Trace has an invalid producer span count.")
        if evidence.state == "complete" and len(events) < expected:
            raise TraceAccessError("Complete trace is missing producer-declared spans.")
    if sealed is not None:
        try:
            if not isinstance(sealed, str) or expected is None:
                raise ValueError
            datetime.fromisoformat(sealed)
        except ValueError:
            raise TraceAccessError("Trace has an invalid producer seal.") from None
    metadata = dict(evidence.metadata)
    if trusted_callback and evidence.state == "complete":
        metadata["completeness_basis"] = (
            "producer_seal" if expected is not None and sealed is not None else "trusted_callback_declaration"
        )
    state = evidence.state
    content_events = [event for event in included if event.kind in ("model", "tool")]
    if state == "complete" and any(capture_withheld(event) for event in included):
        state = "withheld"
    if state == "complete":
        absent = [event for event in content_events if not visible_content(event)]
        if not content_events or absent:
            state = (
                "withheld"
                if not content_events
                or any(event.attributes.get("everybench.content_state") in ("withheld", "redacted") for event in absent)
                else "incomplete"
            )
    result = _ValidatedEvidence.model_validate(
        {
            **evidence.model_dump(),
            "events": included,
            "state": state,
            "run_id": case.run_id,
            "sample_id": case.sample_id,
            "attempt": case.attempt,
            "metadata": metadata,
        }
    )
    result._excluded_events = [event.model_copy(deep=True) for event in events if event.id in excluded]
    return result


def _from_payload(
    payload: Any,
    trace_id: str,
    case: AgentInput,
    workspace_id: str | None,
    source: str,
    *,
    authorized_remote_scope: bool = False,
) -> TraceEvidence:
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("trace"), dict)
        or not isinstance(payload.get("spans"), list)
    ):
        raise TraceAccessError("Malformed trace response.")
    header = payload["trace"]
    if authorized_remote_scope and workspace_id is None:
        # The remote API credential authorizes this scope. It is NOT an engine
        # run's local persistence workspace and must never select that workspace.
        workspace_id = header.get("workspace_id")
        if workspace_id is not None and (not isinstance(workspace_id, str) or not workspace_id):
            raise TraceAccessError("Malformed authorized remote workspace.")
    if header.get("trace_id") != trace_id or header.get("workspace_id") != workspace_id:
        raise TraceAccessError("Trace belongs to another trace/workspace.")
    for field in ("run_id", "sample_id"):
        if header.get(field) is not None and header[field] != getattr(case, field):
            raise TraceAccessError("Trace header belongs to another invocation.")
    attrs = header.get("attributes") or {}
    if not isinstance(attrs, dict):
        raise TraceAccessError("Malformed trace header attributes.")
    for key, field in _BINDINGS.items():
        value = attrs.get(key)
        if value is not None and (value != getattr(case, field) or (field == "attempt" and type(value) is not int)):
            raise TraceAccessError("Trace header attributes belong to another invocation.")
    raw_spans = payload["spans"]
    if not raw_spans:
        if header.get("state") == "closed":
            raise TraceAccessError("Closed trace has no actual spans.")
        return TraceEvidence(trace_id=trace_id, state="pending", source=source)
    events = []
    for raw in raw_spans:
        if not isinstance(raw, dict) or raw.get("trace_id") != trace_id:
            raise TraceAccessError("Malformed or mismatched trace span.")
        # GET /api/traces omits per-span workspace_id; the authenticated header
        # owns the response. Local store reads include it and check every span.
        if "workspace_id" in raw and raw["workspace_id"] != workspace_id:
            raise TraceAccessError("Trace span belongs to another workspace.")
        if raw.get("run_id") is not None and raw["run_id"] != case.run_id:
            raise TraceAccessError("Trace span belongs to another run.")
        span_attrs = raw.get("attributes") or {}
        if not isinstance(span_attrs, dict) or raw.get("content_state") not in (
            "present",
            "withheld",
            "redacted",
            "missing",
        ):
            raise TraceAccessError("Malformed span content state/attributes.")
        content_state = raw["content_state"]
        events.append(
            TraceEvent(
                id=raw["span_id"],
                parent_id=raw.get("parent_id"),
                kind=raw["kind"],
                name=raw["name"],
                status=raw.get("status", "ok"),
                start_time=raw.get("start_time"),
                end_time=raw.get("end_time"),
                attributes={**span_attrs, "everybench.content_state": content_state},
                content=raw.get("content") if content_state == "present" else None,
            )
        )
    expected = attrs.get("expected_spans")
    state = "incomplete"
    if header.get("state") == "closed" and expected is not None and attrs.get("sealed_at"):
        state = "complete"
    evidence = TraceEvidence(
        trace_id=trace_id,
        state=state,
        events=events,
        source=source,
        run_id=header.get("run_id"),
        sample_id=header.get("sample_id"),
        metadata={
            "expected_spans": expected,
            "sealed_at": attrs.get("sealed_at"),
            "workspace_id": workspace_id,
            "completeness_basis": "producer_seal" if expected is not None and attrs.get("sealed_at") else "unsealed",
        },
    )
    if header.get("span_count") != len(events):
        raise TraceAccessError("Trace header/span count mismatch.")
    return _validate_evidence(evidence, case, trusted_callback=False)


def _row(obj: Any) -> dict[str, Any]:
    return {
        column.name: value.isoformat() if isinstance(value := getattr(obj, column.name), datetime) else value
        for column in obj.__table__.columns
    }


class StoreTraceSource:
    def __init__(self, *, workspace_id: str | None = None):
        self._workspace_id = workspace_id

    def describe(self) -> dict[str, Any]:
        return {"type": "store", "workspace_id": self._workspace_id}

    def _read(self, trace_id: str, case: AgentInput) -> TraceEvidence:
        with session_scope() as session:
            header = session.get(Trace, trace_id)
            if header is None:
                return _missing(trace_id, "store")
            if header.workspace_id != self._workspace_id:
                raise TraceAccessError("Trace belongs to another workspace.")
            spans = session.scalars(select(Span).where(Span.trace_id == trace_id)).all()
            return _from_payload(
                {"trace": _row(header), "spans": [_row(span) for span in spans]},
                trace_id,
                case,
                self._workspace_id,
                "store",
            )

    async def get_trace(self, trace_id: str, *, case: AgentInput) -> TraceEvidence:
        _request_id(trace_id, case)
        try:
            return await asyncio.to_thread(self._read, trace_id, case)
        except TraceAccessError:
            raise
        except Exception:
            raise TraceAccessError("Could not read authorized local trace.") from None


class HTTPTraceSource:
    """Read the trace API with credentials separate from the invocation target.

    An explicit workspace_id must match exactly. None uses the remote API's
    authenticated workspace scope, independently of local run storage ownership.
    Per-span workspace IDs, when supplied by an API, must match its trace header.
    """

    def __init__(
        self, base_url: str, *, api_key: str | None = None, workspace_id: str | None = None, timeout: float = 40
    ):
        self._url = _validate_url(base_url).rstrip("/")
        _authorization(api_key)
        self._api_key, self._workspace_id, self._timeout = api_key, workspace_id, _timeout(timeout)

    def describe(self) -> dict[str, Any]:
        return {
            "type": "http",
            **_endpoint_description(self._url),
            "workspace_id": self._workspace_id,
            "timeout": self._timeout,
            "authenticated": bool(self._api_key),
        }

    async def get_trace(self, trace_id: str, *, case: AgentInput) -> TraceEvidence:
        _request_id(trace_id, case)
        try:
            headers = {**_authorization(self._api_key), "Accept": "application/json", "Accept-Encoding": "identity"}
            if self._workspace_id is not None:
                headers["x-everybench-workspace"] = self._workspace_id
            async with asyncio.timeout(self._timeout):
                async with httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=self._timeout) as client:
                    async with client.stream("GET", f"{self._url}/api/traces/{trace_id}", headers=headers) as response:
                        if response.status_code == 404:
                            return _missing(trace_id, "http")
                        if (
                            response.status_code != 200
                            or response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                            != "application/json"
                        ):
                            raise ValueError
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise ValueError
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 8_388_608:
                                raise ValueError
                        payload = _strict_json(body.decode("utf-8", errors="strict"))
                        return _from_payload(
                            payload, trace_id, case, self._workspace_id, "http", authorized_remote_scope=True
                        )
        except asyncio.CancelledError:
            raise
        except TraceAccessError:
            raise
        except Exception:
            raise TraceAccessError("Could not read authorized HTTP trace.") from None


class CallbackTraceSource:
    """Explicitly authorize external evidence and assert platform completeness.

    Callback receives (trace_id, case), must return TraceEvidence with actual root
    binding. A complete declaration is trusted only for platform completion, not
    ownership, event validity or visible-content availability.
    """

    def __init__(self, callback: TraceCallable, *, name: str = "external"):
        if not callable(callback):
            raise TypeError("Trace callback must be callable.")
        self._callback, self._name = callback, name

    def describe(self) -> dict[str, Any]:
        return {"type": "callback", "name": self._name, "authorization": "explicit_callback"}

    async def get_trace(self, trace_id: str, *, case: AgentInput) -> TraceEvidence:
        _request_id(trace_id, case)
        try:
            if inspect.iscoroutinefunction(self._callback):
                result = self._callback(trace_id, case)
            else:
                result = await asyncio.to_thread(self._callback, trace_id, case)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, TraceEvidence):
                raise ValueError
        except asyncio.CancelledError:
            raise
        except Exception:
            raise TraceAccessError("External trace callback failed or returned invalid evidence.") from None
        return _validate_evidence(result, case, trusted_callback=True)
