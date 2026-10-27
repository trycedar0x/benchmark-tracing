# Send TypeScript traces to EveryBench

Keep your existing OpenTelemetry (OTel) instrumentation. Configure its exporter to send traces to EveryBench; you do not need an EveryBench TypeScript SDK or a separate collector. This guide covers server-side Node.js, not browser instrumentation. Keep export credentials on the server.

Basic tracing and trace-aware evaluation are separate integrations. Start with export and verify it before adding evaluation correlation and completion.

## 1. Choose a receiver and content policy

Reuse your operator-provided EveryBench endpoint. For an isolated local receiver, run this from the EveryBench repository in a separate terminal:

```bash
unset EVERYBENCH_DATABASE_URL EVERYEVAL_DATABASE_URL BENCHTRACE_DATABASE_URL
export EVERYBENCH_HOME=$(mktemp -d)
EVERYBENCH_INGEST_CONTENT=metadata uv run everybench serve
```

The default endpoint is `http://127.0.0.1:8321`. No web build or model provider is required for ingest. Use HTTPS for a remote deployment. When authentication is enabled, obtain a workspace API key from the operator.

The receiver defaults to `metadata`: names, timing and permitted attributes are retained, but recognized content bodies are withheld. `redacted` retains content with best-effort secret filtering; `full` retains supplied content. Redaction is not a guarantee against disclosure. Never put credentials, cookies or private reasoning into span content or arbitrary attributes.

To retain explicitly permitted bodies, the operator must change `EVERYBENCH_INGEST_CONTENT` and restart the receiver. Producers can request a stricter policy with `x-everybench-content`, but cannot override a stricter receiver. Trace-aware grading requires visible evidence; metadata-only or redacted stored bodies do not satisfy the current Store/HTTP trace-evidence contract. Prefer an isolated, synthetic-data receiver when testing `full` capture.

## 2. Configure the existing exporter

If your existing OTLP exporter reads standard environment variables, configure it with:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:8321/v1/traces
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
# Only when authentication is enabled; supply the real key securely:
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer bt_..."
```

Use the full trace endpoint, including `/v1/traces`. EveryBench accepts OTLP/HTTP protobuf or JSON, not OTLP/gRPC. Explicit exporter options can override environment settings; a hard-coded destination must be updated in code.

For explicit configuration, the following uses the OTel JS SDK 2.x processor API. Install only packages missing from your app, selecting versions compatible with its existing OTel dependency set rather than independently upgrading the provider:

```bash
npm install @opentelemetry/exporter-trace-otlp-proto @opentelemetry/sdk-trace-base
```

In your existing telemetry initialization module:

```typescript
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-proto';
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base';

const key = process.env.EVERYBENCH_API_KEY;
export const everybenchProcessor = new BatchSpanProcessor(
  new OTLPTraceExporter({
    url: process.env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
      ?? 'http://127.0.0.1:8321/v1/traces',
    headers: {
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
      'x-everybench-content': process.env.EVERYBENCH_CONTENT ?? 'metadata',
    },
  }),
);
```

Add `everybenchProcessor` to the `spanProcessors` array in your **existing** `NodeSDK` or tracer-provider constructor. Keep existing resource, context-manager, propagator, sampler and instrumentation settings. Initialize telemetry before importing instrumented application modules. Do not register a second global provider.

To keep another tracing backend, retain its processor in that same array. If it was configured through `NodeSDK.traceExporter`, explicitly wrap that existing exporter in a processor too: an explicit `spanProcessors` array takes precedence over `traceExporter`.

The explicit example uses `EVERYBENCH_API_KEY` and `EVERYBENCH_CONTENT`, not the standard exporter headers variable. Its default content request is `metadata`. To test synthetic visible bodies, set `EVERYBENCH_CONTENT=full` in the app and `EVERYBENCH_INGEST_CONTENT=full` on the isolated receiver.

The standard exporter does not provide the Python EveryBench SDK's disk spool. Use your existing telemetry pipeline's durability controls if required. Preserve your normal graceful-shutdown path so pending batches can be flushed.

## 3. Check span semantics

EveryBench recognizes these attributes on spans you already emit:

| Operation | `gen_ai.operation.name` | Stored kind |
| --- | --- | --- |
| Agent invocation | `invoke_agent` | `agent` |
| Model call | `chat`, `text_completion`, `generate_content` | `model` |
| Tool call | `execute_tool` | `tool` |

OpenInference's `openinference.span.kind` values such as `AGENT`, `LLM` and `TOOL` are also supported. Avoid conflicting kind attributes. Unrecognized spans are retained as generic steps.

Recognized content attributes include `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`, and OpenInference's `input.value` / `output.value`. Encode structured values as JSON strings when required by the OTel attribute API. Capturing bodies is explicit: changing the exporter does not automatically instrument model calls or tools. Record the actual exchanged visible content, not reconstructed events from a final answer.

## 4. Verify without paid calls

After your existing telemetry initialization has run, execute this synthetic fixture inside the app. It tests export and nesting, not real agent behavior. The processor below is the one configured above; use its actual module path in your app.

```typescript
import { ROOT_CONTEXT, trace } from '@opentelemetry/api';
import { everybenchProcessor } from './telemetry';

const tracer = trace.getTracer('everybench-smoke');
const traceId = tracer.startActiveSpan(
  'synthetic tracing check',
  { attributes: { 'gen_ai.operation.name': 'invoke_agent' } },
  ROOT_CONTEXT,
  root => {
    try {
      for (const operation of ['chat', 'execute_tool']) {
        tracer.startActiveSpan(
          `synthetic ${operation}`,
          { attributes: { 'gen_ai.operation.name': operation } },
          child => child.end(),
        );
      }
      return root.spanContext().traceId;
    } finally {
      root.end();
    }
  },
);
await everybenchProcessor.forceFlush();
console.log(traceId);
```

Run in a sampled development configuration; a sampler that drops the fixture will prevent delivery. Use the resulting ID to inspect the stored trace, adding authorization only if required:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $EVERYBENCH_API_KEY" \
  "http://127.0.0.1:8321/api/traces/$TRACE_ID"
```

Expect an agent root with model and tool children. The fixture sends no bodies, so it verifies metadata only. For real content verification, run an authorized synthetic request through your existing instrumentation and inspect the stored content policy. Verify receipt at the receiver rather than treating a flush alone as proof: sampling, queue drops and earlier export failures may still lose spans.

Without a producer seal the trace stays `open`; basic trace inspection does not require sealing.

## 5. Optional: make traces usable for evaluation

Export alone does not implement evaluation. The current HTTP agent transport expects the strict NDJSON contract described in [Evaluate your own agent](evaluating-your-agent.md); it is not a generic arbitrary-URL runner. Preserve your existing agent behavior and only use this transport if the endpoint meets that contract.

For an evaluation-compatible endpoint:

1. Extract the incoming `traceparent` using the existing OTel propagator. Keep that trace ID rather than creating an unrelated trace.
2. Create one actual invocation root classified as `agent`, directly beneath the propagated evaluator parent. The validator expects that parent (or no parent), not an extra exported HTTP-server ancestor. Configure framework HTTP instrumentation accordingly for this evaluation route; do not remove instrumentation globally.
3. Copy the trusted invocation headers to root attributes: `x-everybench-run-id` → `everybench.run_id`, `x-everybench-sample-id` → `everybench.sample_id`, and `x-everybench-attempt` → integer `everybench.attempt`. Validate them at the authorized evaluation boundary. Do not attach authentication headers to spans.
4. Nest actual model/tool spans beneath that root. Keep evaluator/judge traffic outside the agent trace. If returning `x-everybench-trace-id`, set it before streaming and make it match the incoming trace ID exactly.
5. Track the producer's actual per-trace span count, await all invocation work, end the spans and verify export acknowledgement. Disable sampling/drops for evidence capture; do not count only the spans that happened to survive delivery. A response ending or a successful flush by itself does not establish completeness.
6. Declare that count to the same authorized receiver:

```http
POST /api/traces/TRACE_ID/seal
Authorization: Bearer bt_...
Content-Type: application/json

{"expected_spans": 3}
```

`3` is illustrative, not a fixed count. Include every span produced for that trace, and never seal with a guessed count or reduce the count to hide delivery failures. The receiver marks a sealed trace `closed` once the declared number has arrived; a seal certifies delivery count, not disclosure of omitted content. Mark intentionally withheld capture explicitly as described in the [evaluation contract](evaluating-your-agent.md).

The evaluator independently reads the receiver with `HTTPTraceSource`, using separately configured trace authorization. An OTLP exporter is not a historical-read adapter. Missing, incomplete, withheld or inaccessible evidence must remain unscorable when the benchmark requires traces.

## Troubleshooting

- **No trace:** check the destination, authorization/workspace, sampling, initialization order and OTel export diagnostics. Inspect the receiver, not only the app's flush result.
- **Generic spans:** add supported semantic attributes to the existing real operations; exporting alone cannot identify them.
- **No content:** check both producer and receiver policies, and whether instrumentation actually emits recognized content attributes. Changing retention cannot recover previously withheld bodies.
- **Trace remains open:** expected without a seal; do not invent completion just to change the label.
- **Evaluation rejects a visible trace:** check immutable root binding, parent structure, integer attempt, producer seal and required model/tool bodies. Basic trace visibility is not proof of evaluation eligibility.

## References

- [OpenTelemetry NodeSDK configuration](https://open-telemetry.github.io/opentelemetry-js/interfaces/_opentelemetry_sdk-node.NodeSDKConfiguration.html)
- [Official OTLP/HTTP protobuf exporter](https://open-telemetry.github.io/opentelemetry-js/modules/_opentelemetry_exporter-trace-otlp-proto.html)
- [OTel JS 2.0 processor implementation](https://github.com/open-telemetry/opentelemetry-js/blob/v2.0.0/packages/opentelemetry-sdk-trace-base/src/export/BatchSpanProcessorBase.ts)
- [EveryBench evaluation contracts](evaluating-your-agent.md)
