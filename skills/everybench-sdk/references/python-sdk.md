# Python SDK integration reference

## Minimal application integration

`pip install everybench` in a Python 3.12+ application environment. Start or connect to an everybench server; no benchmark or UI build is needed.

This example has no paid model call. Replace the stub with the application's real call only when authorized:

```python
import os
from everybench.sdk import EveryBench

ee = EveryBench(
    endpoint=os.environ.get("EVERYBENCH_URL", "http://127.0.0.1:8321"),
    content="metadata",
    service_name="support-agent",
    # api_key defaults to EVERYBENCH_API_KEY when not supplied
)
try:
    with ee.trace("answer-question", task_id="q-17") as root:
        trace_id = format(root.get_span_context().trace_id, "032x")
        with ee.span("model", kind="model", **{"gen_ai.request.model": "stub"}) as span:
            ee.record(span, "prompt", "What is 17 * 23?")
            tool_args = {"a": 17, "b": 23}  # replace with the real model response
            ee.record(span, "output", tool_args)
        with ee.span("multiply", kind="tool") as span:
            ee.record(span, "arguments", tool_args)
            result = tool_args["a"] * tool_args["b"]
            ee.record(span, "result", result)
    health = ee.flush(timeout=10)
    receipt = health.receipts.get(trace_id)
    delivered = (
        health.pending_batches == 0
        and health.rejected_batches == 0
        and health.refused_spans == 0
        and receipt is not None
        and receipt["state"] == "closed"
    )
    # Return delivered, trace_id and health.to_dict() to the caller.
    # Delivery verification is not an evaluation score.
finally:
    ee.close(timeout=5)
```

`EveryBench` itself is not a context manager; use try/finally for lifecycle management. Its `trace` and `span` methods are context managers, including around awaited application calls.

## Constructor and methods

| Parameter or method | Contract |
| --- | --- |
| `endpoint` | Server base URL, not `/v1/traces`; defaults to localhost:8321 |
| `api_key` | Workspace bearer token; falls back to `EVERYBENCH_API_KEY` |
| `content` | `metadata` (default), `redacted`, or `full` |
| `service_name` | OpenTelemetry resource service name; default `agent` |
| `spool_dir` | Durable local spool root; default under everybench home, then partitioned by endpoint |
| `max_spool_bytes` | Default 256 MiB; full spool refuses new spans |
| `set_global` | Default true; attaches to an existing SDK TracerProvider or creates/sets one. False creates a private provider |
| `flush_interval` | Background export interval, default 0.5 seconds |
| `trace(name, **attributes)` | New root task trace; seals the trace on context exit, including exception exit |
| `span(name, kind="span", **attributes)` | Child span using active OpenTelemetry context |
| `record(span, key, value)` | Store policy-controlled content; non-string values are JSON-encoded |
| `health()` | Snapshot of counters, pending spool and receipts; does not wait |
| `flush(timeout=30)` | Export and wait for delivery within the SDK's timeout behavior; returns Health |
| `close(timeout=5)` | Flush, stop sender, retain undelivered data for replay; returns Health |
| `health.to_dict()` | JSON-serializable health fields |

Health fields are `pending_batches`, `pending_bytes`, `delivered_batches`, `duplicate_batches`, `rejected_batches`, `refused_spans`, `last_error`, and `receipts`. Counters are client-lifetime totals; do not treat a nonempty receipt map alone as successful delivery of the current task.

## Existing framework/OpenTelemetry instrumentation

Prefer the framework's instrumentation to manually reproducing its internal model/tool calls. Install the matching instrumentor in the application's environment. Pass `tracer_provider=ee.provider` explicitly so those spans go through this client's export and content policy:

```python
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

OpenAIAgentsInstrumentor().instrument(tracer_provider=ee.provider)
# Execute Runner.run(...) within an ee.trace(...) task boundary.
```

For LangChain/LangGraph, the equivalent is `LangChainInstrumentor` from `openinference.instrumentation.langchain`. Use the appropriate optional package (`openinference-instrumentation-openai-agents` or `openinference-instrumentation-langchain`), plus the framework and provider integration. These are not dependencies of the base everybench install. Do not instrument the same operation twice.

When integrating with an existing SDK TracerProvider, default `set_global=True` attaches everybench processors to it. Choose a private provider (`set_global=False`) for isolated smoke tests, and explicitly point instrumentors at it. An installed instrumentor on a different provider will not automatically send spans through everybench.

For non-Python producers, point an OTLP/HTTP exporter at `http://127.0.0.1:8321/v1/traces` (protobuf or OTLP JSON), including workspace authorization if enabled. Ingest alone does not seal a trace; a producer can call `POST /api/traces/TRACE_ID/seal` with `{"expected_spans": N}` when it knows the final span count.

## Guarantees and limits

The client applies content policy before writing batches to its durable spool, retries delivery, and uses stable batch IDs for server deduplication. Undelivered batches survive restarts for the same endpoint. Protect spool files and keep workspace identities separate.

`closed` verifies delivery against the declared count. It does not detect omitted instrumentation. `open` means no final seal is known; `incomplete` means received count does not match declared completeness. Check counts and actual operation coverage before claiming trace integrity.

Metadata mode is not a universal secrets scrubber: names, ordinary attributes and exception details can still contain sensitive data. Do not copy credentials into instrumentation. Content capture should be a deliberate, authorized choice.
