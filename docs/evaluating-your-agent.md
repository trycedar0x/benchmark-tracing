# Evaluate your own agent

EveryBench supplies cases and grading, not an agent solver. The shared SDK/CLI engine invokes your callable or strict HTTP endpoint once per selected original case. It does not supply prompts, tools, Docker, research solvers or arbitrary-code capabilities to HTTP agents. Legacy `everybench run` Inspect/Harbor experiments remain available separately. The public catalog remains unchanged; the finance adapters below are experimental and are not official leaderboard parity.

## Consent and setup

Install the optional finance clients with `pip install 'everybench[finance]'` in your own environment. Generic Python benchmarks/callables do not import Inspect or model clients. Named external-finance grading supports explicit `openai/...`, `openrouter/...`, `anthropic/...`, registered `mock/...` profiles, and Inspect's `mockllm/...` provider only. Finance preflight checks dataset hashes, original case selection, supported/client versions, evaluator client configuration and credentials before agent invocation. Anthropic is constrained to `>=1.17.0,<2` for current Inspect compatibility; Anthropic 2.x is incompatible. Models, provider endpoints and credentials must be configured explicitly; there is no default remote evaluator.

Every invocation, including a local callable, requires SDK `allow_paid=True` or CLI `--yes`. This consents to agent and evaluator calls, not an all-inclusive dollar cap. Remote costs remain `null`; case/time limits are not billing guarantees. There are no automatic target retries. Supported finance judges disable both Inspect and SDK retries and disable reasoning-summary capability probes. Custom `Benchmark.grade` implementations own their provider retry, cost and consent behavior.

## Optional finance and Harbor environments

The finance extra requires `openai>=3.4.0,<4` for current Inspect. Harbor's LiteLLM requires `openai<3`. Install either extra independently; `uv --all-extras` or installing finance and Harbor together is intentionally incompatible. Generic HTTP/callable evaluation has no such conflict. Keep separate virtual environments when using both runners:

```sh
# Finance environment (project checkout):
UV_PROJECT_ENVIRONMENT=.venv-finance uv sync --extra finance
# Independent legacy Harbor environment:
UV_PROJECT_ENVIRONMENT=.venv-harbor uv sync --extra harbor
# Installed-package alternatives, each in its own activated venv:
pip install 'everybench[finance]'
pip install 'everybench[harbor]'
```

CI validates these extras in separate jobs. OpenAI 3.37.0 and Anthropic 1.17.0 were verified against real loopback HTTP 429/503 responses with one request per grading call, without contacting any provider.

## HTTP: separate target and trace authorization

```sh
everybench evaluate findeepindicator \
  --url http://127.0.0.1:8000/api/chat --name financial-research-agent --version YOUR_REVISION \
  --token-env FIN_AGENT_TOKEN \
  --trace-url http://127.0.0.1:8765 --trace-token-env EVERYBENCH_API_KEY \
  --sample-id ORIGINAL_DATASET_ID --limit 1 \
  --extractor openai/YOUR_EXPLICIT_MODEL --judge openai/YOUR_EXPLICIT_MODEL \
  --timeout 40 --trace-timeout 10 --grading-timeout 180 \
  --content redacted --yes --json
```

Use `--data-path /path/to/original/data.csv` (or BigFinance JSONL) for offline/cache validation. The supplied file must match the pinned complete dataset SHA-256 and schema; select a subset using `--sample-id` and/or `--limit`, not by editing the dataset. Selection preserves original IDs, references and source order. An omitted path uses the existing immutable public-data cache/loader. FinDeepIndicator requires both an extractor and one judge. BigFinance uses repeatable `--judge` options, no extractor, and a complete visible model/tool trace. A single judge is explicitly reported as a single-judge adaptation. FinDeepIndicator grades only the response; traces are diagnostic.

The endpoint must implement strict financial-app NDJSON: `conversation` with `conversationId`, text `delta` records, then a bare `done`, followed by clean EOF. Requests contain only `{"message": case.input}`; no prior conversation, references, rubric or gold fields. Correlation uses `traceparent`, `x-everybench-run-id`, `x-everybench-sample-id`, `x-everybench-attempt` and the matching optional response `x-everybench-trace-id`. Final-answer text is not execution evidence. HTTP sources require HTTPS or loopback HTTP, no redirects, no URL credentials/query/fragment, and independently configured trace credentials. SDK/CLI endpoints are explicit local operator capabilities; no public arbitrary-target execution API is added.

```python
from everybench.sdk import HttpAgent, HTTPTraceSource, evaluate

report = await evaluate(
    "bigfinance-public-50",
    HttpAgent("http://127.0.0.1:8000/api/chat", token_env="FIN_AGENT_TOKEN",
              name="financial-research-agent", version="your-revision"),
    trace_source=HTTPTraceSource("http://127.0.0.1:8765", api_key=trace_api_key),
    judge_models=["openai/YOUR_EXPLICIT_MODEL"],
    sample_ids=["ORIGINAL_DATASET_ID"], limit=1,
    allow_paid=True,
)
```

`HTTPTraceSource(workspace_id=None)` uses the remote API credential's authorized workspace; an explicit workspace must match. Engine `workspace_id` selects local persistence independently. Local `StoreTraceSource(None)` is strictly unscoped/local. Tokens and token environment names are not persisted in target/source descriptions.

## Callable and neutral benchmark

```python
from everybench.sdk import BenchmarkCase, CallableAgent, GradeResult, evaluate

class ExactBenchmark:
    name = "my-offline-check"
    requires_trace = False

    def describe(self):
        return {"protocol": "my-offline-check-v1", "grading": "exact"}

    def cases(self):
        return [BenchmarkCase(id="original-17", input="What is 2+2?", reference={"answer": "4"})]

    async def grade(self, case, result, evidence):
        return GradeResult(score=float(result.answer == case.reference["answer"]), protocol="my-offline-check-v1")

# your_agent is your existing implementation; it receives only AgentInput.
async def call_existing(case):
    return await your_agent(case.input)

report = await evaluate(
    ExactBenchmark(), CallableAgent(call_existing, name="my-agent", version="your-revision"),
    target_config={"strategy": "two-model", "temperature": 0},
    allow_paid=True,
)
```

Callbacks may be synchronous or asynchronous and return a string or `AgentResult`. `evaluate_sync` has the same arguments and schema; do not use it inside an event loop. The runnable `examples/evaluate_callable.py --yes` is an explicitly offline fixture, not a research agent.

`target_config` is explicitly declared **public identity**, never a place for secrets. Sensitive fields/URLs/headers are dropped recursively before hashing or serialization, even with full content policy. Do not put credentials in arbitrary public strings, target names or versions: automatic redaction cannot recognize every secret in prose. Meaningful non-secret config and version are retained independently from model identities. A config fingerprint is not cryptographic proof of agent code or model revision.

## Trace callback and local SDK/store

A callback is an explicitly trusted platform/project capability. It must return genuine bound events and declare `complete` only after the external platform has established completion. The adapter validates root binding, span structure and visible content; it cannot independently authorize your platform. Do not construct model/tool events from the submitted answer.

```python
from everybench.sdk import CallbackTraceSource, evaluate

async def fetch_platform_trace(trace_id, case):
    # Implement authorization and the real platform completion check here.
    # Return TraceEvidence with captured events, state and invocation binding.
    return await authorized_platform.read_execution(trace_id, case)

report = await evaluate(
    benchmark, existing_target,
    trace_source=CallbackTraceSource(fetch_platform_trace, name="my-authorized-platform"),
    allow_paid=True,
)
```

For a callable instrumented with the native SDK, use the evaluator's immutable invocation binding, rather than creating a new unrelated root:

```python
from everybench.sdk import EveryBench, StoreTraceSource, evaluate

tracer = EveryBench(endpoint="http://127.0.0.1:8765", content="full")

async def call_instrumented(case):
    with tracer.invocation(case):
        # Existing OpenInference/GenAI instrumentations can capture actual calls.
        # Or wrap existing calls and record only the bodies actually exchanged:
        with tracer.span("existing model call", kind="model") as span:
            answer = await existing_model_call(case.input)
            tracer.record(span, "input", case.input)
            tracer.record(span, "output", answer)
    return answer

report = await evaluate(benchmark, call_instrumented, trace_source=StoreTraceSource(), allow_paid=True)
tracer.close()
```

`StoreTraceSource` reads the same configured local DB as the receiver. For a separate receiver/store use `HTTPTraceSource` instead. The receiver must explicitly permit the producer's content policy; metadata-only or withheld bodies cannot support trace-aware grading. `invocation` seals the actual producer span count; delivery failures remain incomplete. Native `record` supports visible `input`, `output`, `tool_arguments`, and `tool_result` evidence aliases; reasoning/private records are not grading evidence. Callback `complete` is a trusted external completion declaration, while Store/HTTP require the producer seal.

A delivery seal certifies the producer's delivered span count, not complete disclosure of content. Producers can mark bounded/partial/omitted capture with non-content attributes `everybench.evidence.withheld=true` and `everybench.evidence.withheld_keys` containing canonical body-key names only. Such evidence remains explicitly withheld even when another input body survives; it cannot silently qualify for trace-aware grading. Explicit evaluator/scorer classifications and descendants are excluded regardless of conflicting semantic attributes; supported visible-message normalization recursively removes recognized reasoning blocks rather than dumping provider response envelopes.

## Reports, failures and comparison

SDK and `--json` return the same `EvaluationReport` (`everybench.agent-evaluation.v1`). The run has `target_kind=external_agent`, a real target descriptor, and nullable `model`; legacy model identities are never fabricated. Legitimate multi-model traces do not trigger legacy single-model drift rejection. Comparison requires matching benchmark, grading protocol/configuration, original case IDs/attempts and case content hashes. External-agent and legacy model runs cannot silently compare.

Reports are persisted at `$EVERYBENCH_HOME/files/evaluations/RUN_ID/report.json` (default home `~/.everybench`). `report_path(report.run_id)` returns the exact path. Existing hashed sample-evidence artifacts contain original references and actual evidence under the chosen policy. Policy is applied **before** report/artifact/new trace content writes: `metadata` withholds answer/reference/evidence bodies, `redacted` performs best-effort secret filtering, and `full` retains supplied content. The returned report is policy-filtered too. Existing producer-owned traces retain their own earlier retention policy; evaluation does not rewrite them.

Use `everybench runs`, `everybench show RUN_ID`, the existing run/sample/evidence/trace API/UI, and `everybench compare RUN_A RUN_B`. `scored` with score `0` means incorrect, not an execution error. `agent_error`, `timeout`, `trace_missing` (including inaccessible/incomplete/withheld), `grading_error`, and `cancelled` carry no score. Errors are sanitized, not arbitrary provider/agent exception strings. An unscorable case makes the run failed; unknown costs remain unknown.

Cancel the SDK task or use existing `everybench cancel RUN_ID`; cancellation persists a report and propagates `CancelledError`. CLI Ctrl-C exits 130 after cancellation. Cancellation is cooperative: Python cannot kill an already-running synchronous callback thread or undo remote side effects. No timeout is a billing/cancellation guarantee for a user-owned remote service.

Finance evaluator calls retain unique non-null diagnostic Inspect roles so legacy usage/conversion does not mistake judge traffic for the evaluated model. Experimental finance helpers now honor the supplied model ID, rather than implicit ambient role substitutions. This is an intentional narrow legacy behavior change; scorer function signatures remain unchanged.

For external targets, the primary CLI/API/UI **Score** is the mean of non-null scored cases, matching report `metrics.mean_score` and comparison. Fully-correct accuracy remains separately labeled, and legacy model-run accuracy semantics are unchanged. Fractional scores are not rounded into incorrect zeroes, and legitimate external multi-model execution is labeled as multiple models, not revision drift. Public numeric token limits affect target/grading fingerprints; credential-bearing token fields remain excluded. See the [bounded acceptance record](user-agent-evaluation-acceptance.md) for the completed live case and its component-level limitations; that record is not permission to rerun it.
