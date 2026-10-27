# User-owned evaluation

Install EveryBench in Python 3.12+. Module-level `everybench.sdk.evaluate` and `evaluate_sync` accept a neutral benchmark or the experimental `findeepindicator`/`bigfinance-public-50` names plus an existing callable/HTTP target. They are not methods on `EveryBench`, the tracing client. CLI `evaluate --help` exposes the same engine for explicit operator-owned HTTP endpoints.

Generic benchmarks implement `name`, `requires_trace`, `describe()`, `cases()` yielding `BenchmarkCase(id,input,reference)`, and async `grade(case,result,evidence)` returning `GradeResult`. AgentInput carries only question and immutable invocation binding, never references. Callables return a string or AgentResult; sync threads cannot be forcibly cancelled. `allow_paid=True` / CLI `--yes` requires actual approval even for fixtures, not a billing guarantee.

HttpAgent sends only `{"message": case.input}` in a new conversation. Responses must be strict UTF-8 NDJSON: conversation ID, text deltas, bare done and clean EOF. No redirects or partial/error streams. URLs must use HTTPS or loopback HTTP. CLI endpoint capabilities are explicit local operator choices, not arbitrary network API fields.

Trace sources are independent: StoreTraceSource, HTTPTraceSource or an explicitly trusted CallbackTraceSource. Capture actual visible model/tool operations with native `tracer.invocation(AgentInput)` or existing instrumentation. Do not derive traces from final answers or request hidden reasoning. Store/HTTP evidence requires producer sealing, correct binding and complete visible content; delivery alone does not prove disclosure.

Install `everybench[finance]` for supported explicit openai/openrouter/anthropic judges or registered mocks for offline tests. FinDeepIndicator requires one judge plus extractor and grades submitted responses. BigFinance public-50 requires distinct judges and complete visible agent evidence. These are experimental adaptations, not official leaderboard parity. Pinned complete datasets are verified; select original sample IDs separately.

Reports use `schema_version: everybench.agent-evaluation.v1`, persisted under EVERYBENCH_HOME/files/evaluations/RUN_ID/report.json. Evaluation defaults to redacted, tracing/ingest to metadata. Errors have null scores; zero is a valid incorrect answer; unknown remote costs stay unknown. Compare matching original case/grading contracts. Protect existing full historical Inspect logs and artifacts.
