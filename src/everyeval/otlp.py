"""OTLP trace ingest: parse OTLP/HTTP requests and store normalized spans.

Spans from any OpenTelemetry instrumentation are accepted. Kinds come from
OpenInference (`openinference.span.kind`) or the GenAI semantic conventions
(`gen_ai.operation.name`); unknown spans are stored as generic spans rather
than rejected. Content-bearing attributes (prompts, outputs, tool arguments)
move into span content, which the content policy then keeps, redacts or drops.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from typing import Any

from google.protobuf import json_format
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from everyeval.db import IngestBatch, Span, Trace, now
from everyeval.redact import POLICIES, apply_policy, scrub_attributes

CONTENT_KEY = re.compile(
    r"^(input\.value|output\.value|llm\.(input|output)_messages\..*|llm\.prompts\..*|retrieval\.documents\..*"
    r"|embedding\.embeddings\..*\.text|.*message\.content.*|tool_call\.function\.arguments|tool\.(arguments|result)"
    r"|gen_ai\.(prompt|completion)(\..*)?|gen_ai\.(input|output)\.messages|gen_ai\.system_instructions"
    r"|gen_ai\.tool\.call\.(arguments|result)|(everyeval|benchtrace)\.content\..*)$"
)

OPENINFERENCE_KINDS = {
    "LLM": "model",
    "TOOL": "tool",
    "AGENT": "agent",
    "EVALUATOR": "scorer",
    "CHAIN": "span",
    "RETRIEVER": "retriever",
    "EMBEDDING": "embedding",
    "RERANKER": "reranker",
    "GUARDRAIL": "guardrail",
}
GENAI_OPERATIONS = {
    "chat": "model",
    "text_completion": "model",
    "generate_content": "model",
    "execute_tool": "tool",
    "invoke_agent": "agent",
    "create_agent": "agent",
    "embeddings": "embedding",
}
TOKEN_ALIASES = {
    "llm.token_count.prompt": "gen_ai.usage.input_tokens",
    "llm.token_count.completion": "gen_ai.usage.output_tokens",
    "llm.model_name": "gen_ai.request.model",
}
STRICTNESS = {p: i for i, p in enumerate(POLICIES)}  # metadata < redacted < full


class OTLPError(ValueError):
    pass


def effective_policy(server_policy: str, requested: str | None) -> str:
    """Producers may ask for a stricter policy than the server's, never a looser one."""
    if requested is None or requested not in STRICTNESS:
        return server_policy
    return requested if STRICTNESS[requested] < STRICTNESS[server_policy] else server_policy


def parse_request(body: bytes, content_type: str) -> ExportTraceServiceRequest:
    request = ExportTraceServiceRequest()
    try:
        if "json" in content_type:
            # OTLP/JSON encodes trace and span ids as hex; protobuf JSON expects base64.
            data = json.loads(body or b"{}")
            for rs in data.get("resourceSpans", []):
                for ss in rs.get("scopeSpans", []):
                    for span in ss.get("spans", []):
                        for key in ("traceId", "spanId", "parentSpanId"):
                            if span.get(key):
                                span[key] = base64.b64encode(bytes.fromhex(span[key])).decode()
                        for link in span.get("links", []):
                            for key in ("traceId", "spanId"):
                                if link.get(key):
                                    link[key] = base64.b64encode(bytes.fromhex(link[key])).decode()
            json_format.ParseDict(data, request, ignore_unknown_fields=True)
        else:
            request.ParseFromString(body)
    except Exception as ex:  # noqa: BLE001 - any parse failure is a client error
        raise OTLPError(f"Could not parse OTLP request: {ex}") from ex
    return request


def _value(v: AnyValue) -> Any:
    which = v.WhichOneof("value")
    if which is None:
        return None
    if which == "array_value":
        return [_value(x) for x in v.array_value.values]
    if which == "kvlist_value":
        return {kv.key: _value(kv.value) for kv in v.kvlist_value.values}
    if which == "bytes_value":
        return base64.b64encode(v.bytes_value).decode()
    return getattr(v, which)


def _time(nanos: int) -> datetime | None:
    return datetime.fromtimestamp(nanos / 1e9, tz=UTC) if nanos else None


def span_kind(attrs: dict[str, Any], name: str) -> str:
    if kind := attrs.get("everyeval.span.kind") or attrs.get("benchtrace.span.kind"):  # old name: benchtrace
        return str(kind)
    if attrs.get("openinference.span.kind"):
        return OPENINFERENCE_KINDS.get(str(attrs["openinference.span.kind"]).upper(), "unknown")
    if attrs.get("gen_ai.operation.name"):
        return GENAI_OPERATIONS.get(str(attrs["gen_ai.operation.name"]), "span")
    return "span"


def normalize(request: ExportTraceServiceRequest) -> list[dict[str, Any]]:
    out = []
    for rs in request.resource_spans:
        resource = {kv.key: _value(kv.value) for kv in rs.resource.attributes}
        service = resource.get("service.name")
        for ss in rs.scope_spans:
            for s in ss.spans:
                if len(s.trace_id) != 16 or len(s.span_id) != 8:
                    raise OTLPError("Span has an invalid trace or span id.")
                raw = {kv.key: _value(kv.value) for kv in s.attributes}
                attrs: dict[str, Any] = {}
                content: dict[str, Any] = {}
                for key, value in raw.items():
                    if CONTENT_KEY.match(key):
                        content[key] = value
                    else:
                        attrs[TOKEN_ALIASES.get(key, key)] = value
                if service:
                    attrs.setdefault("service.name", service)
                if ss.scope.name:
                    attrs.setdefault("otel.scope", ss.scope.name)
                events = [
                    {
                        "name": e.name,
                        "time": _time(e.time_unix_nano).isoformat() if e.time_unix_nano else None,
                        "attributes": {kv.key: _value(kv.value) for kv in e.attributes},
                    }
                    for e in s.events
                ]
                if events:
                    content["events"] = events
                status = "error" if s.status.code == 2 else "ok"
                if s.status.message:
                    attrs["otel.status_message"] = s.status.message
                out.append(
                    {
                        "trace_id": s.trace_id.hex(),
                        "span_id": s.span_id.hex(),
                        "parent_id": s.parent_span_id.hex() if s.parent_span_id else None,
                        "name": s.name or "span",
                        "kind": span_kind(raw, s.name),
                        "status": status,
                        "start_time": _time(s.start_time_unix_nano),
                        "end_time": _time(s.end_time_unix_nano),
                        "attributes": attrs,
                        "content": content or None,
                    }
                )
    return out


def store(
    session: Session, spans: list[dict[str, Any]], *, policy: str, source: str, workspace_id: str | None
) -> set[str]:
    """Upsert spans by (trace_id, span_id) and refresh trace headers. Returns touched trace ids."""
    traces: set[str] = set()
    for raw in spans:
        content, state = apply_policy(raw["content"], policy)
        values = dict(
            parent_id=raw["parent_id"],
            name=raw["name"][:300],
            kind=raw["kind"][:20],
            status=raw["status"],
            start_time=raw["start_time"],
            end_time=raw["end_time"],
            attributes=scrub_attributes(raw["attributes"]),
            content=content,
            content_state=state,
        )
        existing = session.scalars(
            select(Span).where(Span.trace_id == raw["trace_id"], Span.span_id == raw["span_id"])
        ).first()
        if existing is not None:
            if existing.workspace_id != workspace_id:
                raise OTLPError(f"Trace {raw['trace_id']} belongs to another workspace.")
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            session.add(
                Span(
                    workspace_id=workspace_id, trace_id=raw["trace_id"], span_id=raw["span_id"], source=source, **values
                )
            )
        traces.add(raw["trace_id"])
    session.flush()
    for trace_id in traces:
        refresh_trace(session, trace_id, source=source, workspace_id=workspace_id)
    return traces


def refresh_trace(session: Session, trace_id: str, *, source: str, workspace_id: str | None) -> Trace:
    count, start, end = session.execute(
        select(func.count(Span.id), func.min(Span.start_time), func.max(Span.end_time)).where(Span.trace_id == trace_id)
    ).one()
    ids = set(session.scalars(select(Span.span_id).where(Span.trace_id == trace_id)))
    root = next(
        (
            s
            for s in session.scalars(select(Span).where(Span.trace_id == trace_id).order_by(Span.start_time))
            if s.parent_id is None or s.parent_id not in ids
        ),
        None,
    )
    trace = session.get(Trace, trace_id)
    if trace is None:
        trace = Trace(trace_id=trace_id, workspace_id=workspace_id, source=source, state="open")
        session.add(trace)
    trace.span_count, trace.start_time, trace.end_time = count, start, end
    if root is not None:
        trace.name = root.name
    expected = (trace.attributes or {}).get("expected_spans")
    if expected is not None:
        trace.state = "closed" if count >= expected else "incomplete"
    return trace


def ingest(
    session: Session,
    body: bytes,
    content_type: str,
    *,
    batch_id: str | None,
    policy: str,
    source: str,
    workspace_id: str | None,
) -> dict[str, Any]:
    """Store one OTLP request. A repeated batch id is acknowledged without being stored twice."""
    if batch_id:
        prior = session.get(IngestBatch, batch_id)
        if prior is not None:
            return {"batch_id": batch_id, "accepted_spans": prior.span_count, "duplicate": True}
    spans = normalize(parse_request(body, content_type))
    store(session, spans, policy=policy, source=source, workspace_id=workspace_id)
    if batch_id:
        session.add(IngestBatch(batch_id=batch_id, workspace_id=workspace_id, span_count=len(spans)))
    return {"batch_id": batch_id, "accepted_spans": len(spans), "duplicate": False}


def seal(session: Session, trace_id: str, expected_spans: int, workspace_id: str | None) -> dict[str, Any]:
    """Record how many spans the producer sent. The trace is closed once all have arrived."""
    trace = session.get(Trace, trace_id)
    if trace is None:
        trace = Trace(trace_id=trace_id, workspace_id=workspace_id, source="otlp", state="open")
        session.add(trace)
        session.flush()
    elif trace.workspace_id != workspace_id:
        raise OTLPError(f"Trace {trace_id} belongs to another workspace.")
    trace.attributes = {**(trace.attributes or {}), "expected_spans": expected_spans, "sealed_at": now().isoformat()}
    trace = refresh_trace(session, trace_id, source=trace.source, workspace_id=workspace_id)
    return {
        "trace_id": trace_id,
        "expected_spans": expected_spans,
        "received_spans": trace.span_count,
        "state": trace.state,
    }
