#!/usr/bin/env bash
# Send a trace from any language as OTLP/JSON. No SDK needed: this is what an
# OpenTelemetry OTLP/HTTP exporter does when pointed at everybench.
#   uv run everybench serve            # in another terminal
#   bash examples/otlp_json.sh
# With authentication on, also set EVERYBENCH_API_KEY.
set -euo pipefail

URL="${EVERYBENCH_URL:-http://127.0.0.1:8321}"
TRACE=$(openssl rand -hex 16) ROOT=$(openssl rand -hex 8) CHILD=$(openssl rand -hex 8)
NOW=$(date +%s)000000000 LATER=$(( $(date +%s) + 1 ))000000000

curl -sS -X POST "$URL/v1/traces" \
  -H "Content-Type: application/json" \
  ${EVERYBENCH_API_KEY:+-H "Authorization: Bearer $EVERYBENCH_API_KEY"} \
  -d @- <<JSON
{
  "resourceSpans": [{
    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "support-bot"}}]},
    "scopeSpans": [{
      "scope": {"name": "my-app"},
      "spans": [
        {
          "traceId": "$TRACE", "spanId": "$ROOT", "name": "handle-ticket",
          "startTimeUnixNano": "$NOW", "endTimeUnixNano": "$LATER",
          "attributes": [{"key": "gen_ai.operation.name", "value": {"stringValue": "invoke_agent"}}]
        },
        {
          "traceId": "$TRACE", "spanId": "$CHILD", "parentSpanId": "$ROOT", "name": "chat",
          "startTimeUnixNano": "$NOW", "endTimeUnixNano": "$LATER",
          "attributes": [
            {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
            {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o-mini"}},
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "120"}},
            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "35"}}
          ]
        }
      ]
    }]
  }]
}
JSON
echo

# Optional: declare how many spans the trace has, so everybench can mark it closed or incomplete.
curl -sS -X POST "$URL/api/traces/$TRACE/seal" \
  -H "Content-Type: application/json" \
  ${EVERYBENCH_API_KEY:+-H "Authorization: Bearer $EVERYBENCH_API_KEY"} \
  -d '{"expected_spans": 2}'
echo
echo "View it: uv run everybench trace $TRACE --trace-id"
