# Queued user-agent HTTP evaluation

## Authentication boundary

The catalog, target listing and submission resolve the existing `current_workspace` dependency through `auth.authenticate`. API keys use their stored workspace/role; session workspace hints select only stored memberships (an unknown hint falls back to an authorized membership). Viewers may read metadata but all mutations fail before effects. Hidden targets and unknown IDs return the same 404.

Authentication-off operation is local-only: evaluation endpoints require both a loopback TCP peer and loopback Host, reject cross-origin Origin and Fetch Metadata headers, and do not trust forwarded identity headers. Use authentication for shared or reverse-proxied deployments. The CLI serving boundary refuses non-loopback binds without authentication and disables Uvicorn proxy-header rewriting in local mode, so the loopback check observes the TCP peer. Direct ASGI deployments must likewise disable proxy-header rewriting in auth-off mode; shared or reverse-proxied serving requires authentication. Authenticated CLI serving retains proxy scheme handling for Secure cookies.

## Operator targets

Set `EVERYBENCH_TARGETS_FILE` to an operator-owned JSON file outside publicly served/uploaded directories. The file is a list of profiles. IDs must be unique within each exact workspace; the same ID may exist in different workspaces. Explicit `workspace_id: null` means local-only, never public or wildcard access.

```json
[
  {
    "id": "research-agent",
    "name": "Research agent",
    "version": "2026-09-01",
    "workspace_id": "ws_example",
    "url": "https://agent.example/private/chat",
    "token_secret": "AGENT_TOKEN",
    "trace": {
      "type": "http",
      "url": "https://trace.example/private",
      "token_secret": "TRACE_TOKEN"
    },
    "data_path": "/operator/pinned/all.csv"
  }
]
```

`trace` and `data_path` are optional. Store traces use only `{"type":"store"}` and read the run's local workspace. HTTP trace authorization is independent of agent authorization. Remote trace scope does not select local run ownership. URLs use the existing strict transport validation (HTTPS or explicit loopback HTTP, no embedded credentials/query/fragment); redirects are forbidden. HTTP agents must implement the existing conversation/delta/done NDJSON protocol, not a generic JSON answer endpoint. Operator URLs are trusted network capabilities, including intentional loopback services. Members cannot register targets through this API.

Credentials reference encrypted workspace secrets. Execution resolves exactly `secrets_for(run.workspace_id)`, never global/null/another-workspace fallbacks. Supported evaluator credentials are `OPENAI_API_KEY`, `OPENROUTER_API_KEY` and `ANTHROPIC_API_KEY`; only those provider variables are installed in the dedicated child environment. Arbitrary workspace secret names cannot override process/database/Python settings. The shared worker never receives workspace credentials in its environment. Its child starts with a small platform/configuration environment allowlist, not inherited provider credentials or proxy/Python settings.

## Metadata and submission

`GET /api/catalog` returns only FinDeepIndicator and BigFinance public 50 without loading datasets or constructing providers. Each entry has `id`, `name`, `description`, `experimental`, `official_parity`, `count`, `requires_trace`, `requires_extractor`, `judge_requirement`, `license`, `license_url`, `attribution` and `revision`. These adaptations are experimental, not official leaderboard parity. BigFinance requires complete visible trace evidence; FinDeepIndicator grades submitted responses.

`GET /api/evaluation-targets` returns only exactly scoped profiles as `{id,name,version,origin,has_trace_source}`. Endpoint paths, credential names and values are never returned.

`POST /api/runs` returns **one queued run object**, HTTP 201. Accepted fields:

- Required: `benchmark`, `target_id`, distinct explicit `judge_models`, boolean `allow_paid: true`.
- Optional: `extractor_model`, distinct nonempty `sample_ids`, `limit` (integer 1–3350, also bounded by benchmark count), `timeout` (greater than zero, at most 3600 seconds), `trace_timeout` (0–3600), `grading_timeout` (greater than zero, at most 3600), `content_policy` (`metadata`, `redacted`, `full`; default `redacted`).
- Defaults: timeout 40, trace timeout 10, grading timeout 180 seconds. At most ten judge IDs and 3350 selected IDs.
- FinDeepIndicator requires exactly one judge and an extractor. BigFinance forbids an extractor and requires a configured trace source. Unknown fields, solver model lists, URLs, paths, secret/environment names, workspace/role overrides, quotes, epochs and budgets are rejected. False, numeric or string consent is rejected.

Structural errors are 422; incompatible grading is 400; unknown benchmark/target is 404. Authentication errors are 401 and authorization/local-origin failures are 403. Operator configuration diagnostics are sanitized. Selection IDs are checked against pinned data during execution preflight, before agent invocation. Consent is not a cost quote or cap: remote-agent/evaluator costs are unknown and not capped.

## Lifecycle and retained features

Run plus job commit in one transaction. The run's immutable submission snapshot is stored under `manifest.evaluation_submission`, including the resolved workspace, target ID, profile SHA-256 and validated request. No endpoint or credential reference/value is stored there. The child verifies scope and exact profile fingerprint again before resolving secrets; any changed profile fails closed. The shared evaluation engine binds the existing run ID via an internal context, preserving the public SDK `evaluate` signature and preventing duplicate run creation.

Queued cancellation prevents invocation; running cancellation is cooperatively observed by the engine, which saves partial results and a cancelled report. A supervisor kills an unresponsive cancelling child after a grace period. Stale jobs fail without replay, preserving partial results. A surviving child checks persisted run ownership before starting each agent, trace or grading coroutine, during bounded waits and after completed waits; trace polling checks before each source request too. Any non-running or missing run stops further work. Stale failure remains failed with its original reason and finish time; the interrupted case is unscored, not an incorrect answer, and already committed evidence is retained in the partial report. In-flight remote work cannot be undone. SDK coroutine cancellation still persists partial results and propagates cancellation. Preflight failures, sample grading errors and evidence/report persistence errors have separate sanitized statuses/diagnostics. Raw provider exceptions and child stdout/stderr are not exposed. Agent/evaluator calls are not retried automatically.

Historical runs (including old model runs), samples, evidence, traces, imports, datasets and paired comparisons remain available through existing read/cancel APIs. Legacy execution is refused; `/api/models` and all `/api/quotes` routes are removed. No historical tables/columns/data are dropped. Report JSON remains at the existing evaluation report path.

## Offline verification

`tests/test_backend_queue.py` drives POST → queued Job → actual separate Python child → shared engine → same persisted run/report with a loopback NDJSON agent, pinned synthetic finance data and a deterministic mock judge installed only by the test bootstrap. No toy adapter appears in public metadata. Security tests cover API keys, session hints, viewers, exact/null scope, independent trace credentials, missing secrets, parent environment isolation and changed profiles. Server tests retain old model-run read/comparison/trace/evidence fixtures.
