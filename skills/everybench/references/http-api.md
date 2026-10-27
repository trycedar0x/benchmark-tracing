# HTTP API reference for coding agents

Use a running everybench server, default `http://127.0.0.1:8321`. API-only operation does not require a web build. `everybench serve` starts two embedded worker slots; `--workers 0` requires a separate `everybench worker` sharing its database.

Read the running server's `/openapi.json` for its installed contract; interactive API docs are at `/docs`. This reference describes the repository version of the API.

## Connection and lifecycle

- With auth enabled, send `Authorization: Bearer bt_...` on API/OTLP requests. Use a workspace API key supplied by the operator. Viewer keys cannot create runs.
- Set explicit per-request timeouts and an overall polling deadline. Poll GETs about once a second; do not poll forever.
- `GET /api/health` returns `ok`, `queued_jobs` and `embedded_workers`. Zero embedded workers is valid if an external worker is running.
- POST creation endpoints are not documented as idempotent. On an ambiguous transport failure, inspect existing state before submitting again.
- Handle non-2xx responses before parsing success payloads. Authentication, validation and approval errors are blockers, not permission to weaken server settings.
- Run terminal states: `succeeded`, `failed`, `cancelled`, `budget_exceeded`. Stop waiting at any terminal state; only `succeeded` is eligible for a strict gate.

## Discovery and runs

| Request | Response or purpose |
| --- | --- |
| `GET /api/catalog` | Public entries with ref, variant identity, dependencies and sandbox requirements; internal fixtures excluded |
| `GET /api/catalog/gsm8k@1` | One public entry |
| `GET /api/models` | Configured prices, not a complete provider model catalog |
| `POST /api/runs` | Legacy model/Harbor jobs returned in request model order; no arbitrary external target URL |
| `GET /api/runs?limit=50&include_quotes=false` | Recent authorized runs with benchmark/status filters; limit at most 500 |
| `GET /api/runs/RUN_ID` | Run detail, target/model identity, manifest, status, scores/errors and outcome counts |
| `GET /api/runs/RUN_ID/samples?offset=0&limit=100` | `{"total": N, "items": [...]}`; page to total, limit at most 1000 |
| `GET /api/runs/RUN_ID/sample-evidence?sample_id=ORIGINAL_ID&epoch=1` | Policy-filtered sample artifact when available; original references remain evaluator-only |
| `GET /api/runs/RUN_ID/grading-diagnostic?sample_id=ORIGINAL_ID&epoch=1` | Evaluator-only grading excerpt after a format failure; `agent_evidence` is false and the body is not a trace |
| `POST /api/runs/RUN_ID/cancel` | Request cooperative cancellation |
| `GET /api/compare?a=BASELINE_ID&b=CANDIDATE_ID` | Paired comparison; inspect compatibility/results even on HTTP 200 |

Offline create body:

```json
{"benchmark":"toy-arith@1","models":["mock/strong","mock/weak"],"limit":20,"epochs":1,"content_policy":"metadata"}
```

Paid models require an approved quote by default. Direct run creation also accepts `budget_usd` (per run) when server policy allows it. Do not disable quote enforcement for automation.

The HTTP run and quote request schemas currently do **not** accept a custom `agent` class. Use CLI `--agent package.module:AgentClass` for Harbor custom agents after approval to execute that code. Do not send guessed API fields.

Public catalog discovery excludes developer fixtures such as `toy-arith`; explicit internal mock POSTs remain available for the bundled development smoke helper. Use a public ref such as `gsm8k@1` for detail discovery. External-agent execution uses the separately authorized SDK/CLI path in [user-agents.md](user-agents.md), not extra fields guessed onto `/api/runs`. Persisted external run details expose `target_kind`, `target`, nullable legacy `model` and primary `score`; legacy `accuracy` remains fully-correct accuracy.

## Quote, approve, run

Creating a quote executes sample calls and requires spending authorization **before** this flow:

1. `POST /api/quotes` with `{"benchmark":"gsm8k@1","models":["PROVIDER/BASELINE","PROVIDER/CANDIDATE"],"limit":50,"sample_size":5}`.
2. Save its `id`. Poll `GET /api/quotes/ID` with a deadline. The quote starts as `estimating` while its job is queued or running, then becomes `draft` or `failed`.
3. Read its estimate/errors and ask the operator to approve a cap. `POST /api/quotes/ID/approve` with `{"cap_usd":5}` is an authorization action, not routine polling.
4. The quote becomes `approved`. `POST /api/runs` with `{"quote_id":"ID","content_policy":"metadata"}` consumes it and returns runs.
5. The quote becomes `used`; do not assume it can be reused.

The cap covers all quote models and is divided equally into per-run caps. Quote-sampling spend is separate from the approved full-run cap. Real Inspect-provider caps require configured pricing. Caps are stop thresholds, not hard billing guarantees: in-flight calls may overshoot.

## Traces and trajectory diff

`GET /api/traces?run_id=RUN_ID&limit=100` returns recent trace headers, not a paginated complete run export; limit at most 1000. For exhaustive sample-to-trace mapping, page through the run's samples instead.

`GET /api/traces/TRACE_ID` returns `trace`, `sample` (may be null for standalone SDK traces), and `spans`. A sample's `trace_id` or comparison row's `a_trace_id`/`b_trace_id` supplies the ID. Preserve epoch when selecting samples.

`GET /api/trace-diff?a=TRACE_A&b=TRACE_B` returns `a`, `b`, `summary`, zero-based `first_divergence` (or null), and aligned `pairs`. Each pair has `op` (`same`, `changed`, `only_a`, `only_b`), step data and `differences`. A first divergence is evidence of changed behavior, not automatically the root cause.

For new instrumentation, use the separate `everybench-sdk` skill. OTLP ingest is `POST /v1/traces`; it is not a benchmark-run endpoint.
