# Evaluate a user-owned agent

## Choose the execution path

The public module functions `everybench.sdk.evaluate` / `evaluate_sync` and CLI `everybench evaluate` share the external-agent engine. Reuse the existing agent's callable or strict HTTP/NDJSON endpoint; do not recreate its prompts, tools or solver. Invocation and trace acquisition are separate adapters. No Docker is required for this path. `EveryBench` is the tracing client; it has no instance `ee.evaluate()` method.

The bundled `scripts/evaluate.py` remains a legacy model/Harbor baseline/candidate API gate. It does not accept external target URLs. `/api/runs` is not a public arbitrary-agent invocation proxy. Keep those interfaces and their schemas distinct.

## Explicit HTTP CLI example

This is configuration syntax, not authorization to spend. Set the target token and trace API key independently without printing them. Choose explicit real evaluator model IDs only after approval:

```bash
everybench evaluate findeepindicator \
  --url https://YOUR_AGENT/api/chat --name YOUR_AGENT --version YOUR_REVISION \
  --token-env AGENT_TOKEN \
  --trace-url https://YOUR_TRACE_SERVER --trace-token-env TRACE_API_KEY \
  --sample-id ORIGINAL_ID --limit 1 \
  --extractor openrouter/YOUR_EXPLICIT_MODEL --judge openrouter/YOUR_EXPLICIT_MODEL \
  --timeout 40 --trace-timeout 10 --grading-timeout 180 \
  --content redacted --yes --json
```

The CLI persists to its configured local `EVERYBENCH_HOME`/database while calling these explicit agent/trace endpoints; it does not schedule the evaluation on `EVERYBENCH_URL`. Targets must accept exactly `{"message": "..."}` and return strict conversation/delta/done NDJSON with clean EOF. Fresh conversations, bounded bytes/deadlines, no redirects and no automatic retries are enforced. A JSON answer endpoint, partial stream or HTTP 200 alone is not compatible/completed success.

Finance adapters are experimental named integrations, hidden from the public catalog. `findeepindicator` needs an extractor and one formula judge; its trace is diagnostic. `bigfinance-public-50` accepts repeatable `--judge` but no extractor and requires complete genuine visible model/tool evidence. Single-judge adaptation is declared, not official leaderboard parity. `--data-path` must reference the pinned complete original CSV/JSONL; select original cases with `--sample-id`/`--limit`, not by altering data. References/rubrics are never sent to the target.

Finance installs supported optional SDKs with `everybench[finance]`; use a separate environment from `everybench[harbor]` because current OpenAI SDK constraints conflict. Explicit evaluator providers are OpenAI/OpenRouter and compatible Anthropic (>=1.17,<2), plus registered offline mocks. Preflight rejects unsupported/missing clients before spending an agent invocation. Custom Python benchmarks may implement their own grading contract; those implementations own their provider behavior.

## Tracing and authorization

An authorized source supplies bound genuine events via StoreTraceSource, HTTPTraceSource or CallbackTraceSource. Standard OTLP ingest is not a historical-query protocol. Correlation headers are `traceparent`, `x-everybench-run-id`, `x-everybench-sample-id`, `x-everybench-attempt`; the optional response header is `x-everybench-trace-id`. Agent roots must carry matching `everybench.run_id/sample_id/attempt` attributes. Tokens belong in auth configuration, never spans.

A producer seal verifies declared span delivery, not complete visible capture. Metadata-only, withheld, missing or partially captured trace content cannot support BigFinance grading; never substitute evaluator traffic or manufacture model/tool actions from the final answer. Use real framework instrumentation or the tracing client's `invocation(case)` boundary and record only actually exchanged visible bodies. Hidden reasoning is not requested evidence. Callback authorization/completeness remains the callback author's trusted responsibility.

## Interpret results before gating

`--json` / SDK return `schema_version: everybench.agent-evaluation.v1`; this differs from the legacy helper's `schema: everybench.agent-eval/v1`. Save the actual run ID and report path (`$EVERYBENCH_HOME/files/evaluations/RUN_ID/report.json`). External targets have `target_kind=external_agent`, real target identity/version/config and nullable legacy `model`.

Inspect each result's `status`, `score`, `components`, `trace_state` and sanitized error. Scored zero is an incorrect result, not infrastructure failure. Agent/time/trace/grading/cancellation failures have null scores. External primary Score is the mean over non-null scores; exact-one accuracy stays separate. Unknown total remote cost remains null; case/time limits and `--yes` are not billing guarantees. No automatic target/judge retry is supplied; ambiguous results require inspecting state and fresh authorization, not replaying blindly.

For comparison, preserve A=baseline/B=candidate, original case IDs/attempts/content and grading protocol/model configuration. Use `everybench compare A B --json` or the authenticated read API; reject incompatible/partial/error results under an explicit gate policy. A legitimate multi-model external agent is not legacy revision drift. Fractional scores and confidence intervals are fractions; one passing case is not representative benchmark performance or deployment approval.
