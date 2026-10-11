"""Trace your own agent into everyeval, without losing spans on crashes.

    from everyeval.sdk import EveryEval

    ee = EveryEval(endpoint="http://127.0.0.1:8321")
    with ee.trace("answer-question", task_id="q-17"):
        with ee.span("call-model", kind="model", **{"gen_ai.request.model": "gpt-4o-mini"}) as span:
            ...
    receipt = ee.flush()

Built on the OpenTelemetry SDK, so OpenInference and GenAI instrumentations
(OpenAI, Anthropic, LangChain, ...) are captured too. Delivery guarantees:

- The content policy is applied before anything is written to disk.
- Every batch is written to a local spool (fsync + atomic rename) before it is
  sent, and deleted only after the server acknowledges it. Batches left by a
  crash are delivered by the next client using the same endpoint.
- Each batch has a stable id, so a retry after a lost acknowledgement is not
  stored twice.
- A spool is tied to one endpoint; batches are never sent somewhere else.
- When the spool is full, new spans are refused and counted, never silently dropped.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from everyeval.config import env, home_dir
from everyeval.otlp import CONTENT_KEY
from everyeval.redact import POLICIES, SENSITIVE_KEYS, redact_text

log = logging.getLogger("everyeval.sdk")

DEFAULT_MAX_SPOOL_BYTES = 256 * 1024 * 1024


@dataclass
class Health:
    pending_batches: int = 0
    pending_bytes: int = 0
    delivered_batches: int = 0
    duplicate_batches: int = 0
    rejected_batches: int = 0
    refused_spans: int = 0
    last_error: str | None = None
    receipts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Spool:
    """Durable on-disk queue of encoded batches, oldest first."""

    def __init__(self, directory: Path, destination: str, max_bytes: int) -> None:
        self.dir = directory
        self.rejected_dir = directory / "rejected"
        self.max_bytes = max_bytes
        self.rejected_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = directory / "destination"
        if marker.exists() and marker.read_text() != destination:
            raise RuntimeError(f"Spool {directory} belongs to {marker.read_text()}, not {destination}")
        marker.write_text(destination)

    def pending(self) -> list[Path]:
        return sorted(p for p in self.dir.iterdir() if p.suffix in (".otlp", ".seal"))

    def size(self) -> int:
        total = 0
        for p in self.pending():
            try:
                total += p.stat().st_size
            except FileNotFoundError:
                pass
        return total

    def write(self, data: bytes, suffix: str) -> Path | None:
        if self.size() + len(data) > self.max_bytes:
            return None
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex}{suffix}"
        tmp = self.dir / f".{name}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        final = self.dir / name
        os.replace(tmp, final)
        fd = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return final

    def remove(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def reject(self, path: Path, reason: str) -> None:
        try:
            os.replace(path, self.rejected_dir / path.name)
            (self.rejected_dir / f"{path.name}.reason").write_text(reason)
        except FileNotFoundError:
            pass


def _filter_value(value: Any, policy: str) -> None:
    """Redact string leaves of an OTLP AnyValue in place."""
    which = value.WhichOneof("value")
    if which == "string_value":
        value.string_value = redact_text(value.string_value)
    elif which == "array_value":
        for v in value.array_value.values:
            _filter_value(v, policy)
    elif which == "kvlist_value":
        for kv in value.kvlist_value.values:
            _filter_value(kv.value, policy)


def _filter_attributes(attributes: Any, policy: str) -> None:
    keep = []
    for kv in attributes:
        if CONTENT_KEY.match(kv.key):
            if policy == "metadata":
                continue
            if policy == "redacted":
                _filter_value(kv.value, policy)
        if SENSITIVE_KEYS.search(kv.key):
            kv.value.Clear()
            kv.value.string_value = "[REDACTED:field]"
        keep.append(kv)
    if len(keep) != len(attributes):
        copies = [type(kv)() for kv in keep]
        for copy, kv in zip(copies, keep, strict=True):
            copy.CopyFrom(kv)
        del attributes[:]
        attributes.extend(copies)


def apply_policy_to_request(request: Any, policy: str) -> None:
    for rs in request.resource_spans:
        _filter_attributes(rs.resource.attributes, "redacted")
        for ss in rs.scope_spans:
            for span in ss.spans:
                _filter_attributes(span.attributes, policy)
                for event in span.events:
                    if policy == "metadata":
                        del event.attributes[:]
                    else:
                        _filter_attributes(event.attributes, policy)


class _SpoolExporter(SpanExporter):
    def __init__(self, client: EveryEval) -> None:
        self.client = client

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        request = encode_spans(spans)
        apply_policy_to_request(request, self.client.content)
        try:
            path = self.client.spool.write(request.SerializeToString(), ".otlp")
        except OSError as ex:
            self.client._record_refused(len(spans), f"Spool write failed: {ex}")
            return SpanExportResult.FAILURE
        if path is None:
            self.client._record_refused(len(spans), "Spool is full; spans refused.")
            return SpanExportResult.FAILURE
        self.client._wake.set()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


class _SpanCounter(SpanProcessor):
    """Counts ended spans per trace, so a sealed trace declares how many spans it produced."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = defaultdict(int)
        self.lock = threading.Lock()

    def on_end(self, span: ReadableSpan) -> None:
        with self.lock:
            self.counts[format(span.context.trace_id, "032x")] += 1

    def pop(self, trace_id: str) -> int:
        with self.lock:
            return self.counts.pop(trace_id, 0)


class EveryEval:
    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:8321",
        *,
        api_key: str | None = None,
        content: str = "metadata",
        service_name: str = "agent",
        spool_dir: str | Path | None = None,
        max_spool_bytes: int = DEFAULT_MAX_SPOOL_BYTES,
        set_global: bool = True,
        flush_interval: float = 0.5,
    ) -> None:
        if content not in POLICIES:
            raise ValueError(f"content must be one of {POLICIES}")
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key or env("API_KEY")
        self.content = content
        base = Path(spool_dir) if spool_dir else home_dir() / "spool"
        digest = hashlib.sha256(self.endpoint.encode()).hexdigest()[:12]
        self.spool = Spool(base / digest, self.endpoint, max_spool_bytes)
        self._health = Health()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()

        self.counter = _SpanCounter()
        self.processor = BatchSpanProcessor(_SpoolExporter(self), schedule_delay_millis=int(flush_interval * 1000))
        current = otel_trace.get_tracer_provider()
        self._owns_provider = not (set_global and isinstance(current, TracerProvider))
        if not self._owns_provider:
            # Attach to the application's existing provider instead of replacing it.
            self.provider = current
        else:
            self.provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
            if set_global:
                otel_trace.set_tracer_provider(self.provider)
        self.provider.add_span_processor(self.counter)
        self.provider.add_span_processor(self.processor)
        self.tracer = self.provider.get_tracer("everyeval.sdk")

        self._sender = threading.Thread(target=self._send_loop, name="everyeval-sender", daemon=True)
        self._sender.start()
        atexit.register(self.close)

    # -- instrumentation helpers

    @contextmanager
    def trace(self, name: str, **attributes: Any) -> Iterator[otel_trace.Span]:
        """Root span for one task. On exit, including by an exception, the trace is sealed with its span count."""
        trace_id = None
        try:
            with self.tracer.start_as_current_span(
                name,
                attributes={"everyeval.span.kind": "agent", **attributes},
                context=otel_trace.set_span_in_context(otel_trace.INVALID_SPAN),
            ) as span:
                trace_id = format(span.get_span_context().trace_id, "032x")
                try:
                    yield span
                except BaseException as ex:
                    span.record_exception(ex)
                    span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, str(ex)))
                    raise
        finally:
            if trace_id is not None:
                self._seal(trace_id)

    def _seal(self, trace_id: str) -> None:
        self.processor.force_flush()
        expected = self.counter.pop(trace_id)
        self.spool.write(json.dumps({"trace_id": trace_id, "expected_spans": expected}).encode(), ".seal")
        self._wake.set()

    @contextmanager
    def span(self, name: str, kind: str = "span", **attributes: Any) -> Iterator[otel_trace.Span]:
        with self.tracer.start_as_current_span(name, attributes={"everyeval.span.kind": kind, **attributes}) as s:
            yield s

    @staticmethod
    def record(span: otel_trace.Span, key: str, value: Any) -> None:
        """Attach content (prompt, output, tool result). The content policy decides whether it is kept."""
        span.set_attribute(f"everyeval.content.{key}", value if isinstance(value, str) else json.dumps(value))

    # -- delivery

    def _record_refused(self, count: int, reason: str) -> None:
        with self._lock:
            self._health.refused_spans += count
            self._health.last_error = reason
        log.warning("everyeval: %s (%d spans)", reason, count)

    def _post(self, path: str, data: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
        headers = dict(headers)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.endpoint + path, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read()

    def _deliver(self, path: Path) -> bool:
        """Send one spooled item. Returns False when delivery should pause and retry later."""
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return True
        batch_id = path.name.split(".")[0]
        try:
            if path.suffix == ".otlp":
                status, body = self._post(
                    "/v1/traces",
                    data,
                    {
                        "Content-Type": "application/x-protobuf",
                        "x-everyeval-batch-id": batch_id,
                        "x-everyeval-content": self.content,
                        "x-everyeval-source": "sdk",
                    },
                )
            else:
                record = json.loads(data)
                status, body = self._post(
                    f"/api/traces/{record['trace_id']}/seal",
                    json.dumps({"expected_spans": record["expected_spans"]}).encode(),
                    {"Content-Type": "application/json"},
                )
        except (urllib.error.URLError, OSError, TimeoutError) as ex:
            with self._lock:
                self._health.last_error = f"Delivery failed: {ex}"
            return False

        if 200 <= status < 300:
            self.spool.remove(path)
            with self._lock:
                self._health.delivered_batches += 1
                if path.suffix == ".seal":
                    receipt = json.loads(body)
                    self._health.receipts[receipt["trace_id"]] = receipt
            return True
        message = body.decode(errors="replace")[:500]
        if status in (408, 429) or status >= 500:
            with self._lock:
                self._health.last_error = f"Server returned {status}: {message}"
            return False
        self.spool.reject(path, f"{status}: {message}")
        with self._lock:
            self._health.rejected_batches += 1
            self._health.last_error = f"Batch rejected ({status}): {message}"
        log.error("everyeval: batch %s rejected (%s): %s", batch_id, status, message)
        return True

    def _send_loop(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            self._wake.clear()
            paused = False
            for path in self.spool.pending():
                if not self._deliver(path):
                    paused = True
                    break
            if paused:
                self._wake.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            else:
                backoff = 0.5
                self._wake.wait(1.0)

    def health(self) -> Health:
        pending = self.spool.pending()
        with self._lock:
            self._health.pending_batches = len(pending)
            self._health.pending_bytes = self.spool.size()
            return Health(**{**asdict(self._health), "receipts": dict(self._health.receipts)})

    def flush(self, timeout: float = 30.0) -> Health:
        """Export buffered spans and wait until the spool is delivered or the timeout passes."""
        deadline = time.monotonic() + timeout
        self.processor.force_flush(int(timeout * 1000))
        self._wake.set()
        while self.spool.pending() and time.monotonic() < deadline:
            time.sleep(0.05)
            self._wake.set()
        return self.health()

    def close(self, timeout: float = 5.0) -> Health:
        """Flush with a deadline and stop the sender. Undelivered batches stay spooled for next time."""
        if self._stop.is_set():
            return self.health()
        health = self.flush(timeout)
        self._stop.set()
        self._wake.set()
        if self._owns_provider:
            self.provider.shutdown()
        else:
            self.processor.shutdown()
        self._wake.set()
        try:
            atexit.unregister(self.close)
        except Exception:  # noqa: BLE001
            pass
        if health.pending_batches:
            log.warning("everyeval: %d batch(es) still spooled; they will be sent next time.", health.pending_batches)
        return health
