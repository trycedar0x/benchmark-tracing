# User-owned agent evaluation: acceptance record

Implementation and bounded local acceptance completed on 2026-10-26. This is a functional integration check, not official leaderboard parity or a representative finance benchmark result.

## Implemented path

The CLI and SDK share the external-agent evaluation engine: original cases → existing callable/HTTP agent → completed answer and independently acquired real trace → benchmark scorer → policy-filtered persisted report and comparison. Inspect/Harbor remain separate compatibility runners; the external HTTP path requires neither Docker nor an EveryBench-created research solver.

The financial app received optional observability only. Its prompt, configured model, bounded SEC tools, authorization, NDJSON protocol, history isolation and resource limits were preserved. The neutral finance adapters remain experimental and do not appear in the public catalog, which still contains 23 entries. Usage: [Evaluate your own agent](evaluating-your-agent.md).

## PR delivery scope

This PR delivers **EveryBench only**, as explicitly selected by the operator. The financial app's observability changes exist in a separate local directory outside this Git repository and are **not included or published by this PR**. The live result and app test counts below are local acceptance evidence, not proof that a standalone checkout contains or can reproduce that app revision.

The checked-in cross-repository fixture intentionally skips when the compatible built sibling app is absent, including ordinary CI checkouts. Generic SDK/HTTP/trace/scoring tests remain self-contained. To run those six integration cases, supply the separately instrumented app and build it first; the PR does not install, vendor or deploy it. Private live reports/databases/logs and credentials are not committed.

## Offline validation and review

- Parent reran the final Python suite: **496 passed, 7 skipped**. The skips require optional Docker/Harbor, OpenAI Agents or LangGraph installations.
- Financial app: **50 tests passed**, typecheck and build passed.
- Browser: **11 passed, 1 skipped**; the existing authentication test requires `AUTH_URL`.
- Provider loopback tests: **18 passed**, including OpenAI/OpenRouter/Anthropic 429/503 responses with one request per grading call. No real provider calls were used in these tests.
- Lint, whitespace checks, lock consistency, package build and actual web rebuild passed. Existing bundle-size/future-loader and seven unrelated UI lint warnings remain.
- Fresh reviews initially blocked on privacy/evidence/scoring defects. Accepted findings were fixed with regressions; final tracing review and credential-filter review found no remaining issues. Reviewers inspected sources rather than independently rerunning tests. The final credential review noted that full historical before-images were unavailable for three untracked files; exact recorded mutations and file hashes were supplied instead.

The real local HTTP/OTLP integration tests exercise successful scoring, receiver metadata clamping, missing traces, partial streams, oversized withheld output and secrets in metadata attributes. They assert reference isolation, real model-action/tool linkage, producer acknowledgement before sealing, exclusion of evaluator traffic and no judging when required evidence is withheld.

## Single authorized live case

The operator's earlier “允许实测” approval in this interactive session was consumed by exactly one agent invocation and its necessary extraction/formula grading. There was no automatic retry, deployment or commit. This record does not authorize any future execution.

| Field | Result |
| --- | --- |
| Benchmark / original sample | FinDeepIndicator / `1799` |
| Question | Freeport-McMoRan's 2025 Liability-to-Asset Ratio, under US GAAP, with formula/raw data/calculation |
| Selection | One declared US-GAAP ratio case relevant to the app's existing tools; not a random or representative benchmark sample |
| Dataset revision | `f59dad280fba4e736ccf346eb234265d13fc6737` |
| Complete dataset SHA-256 | `5dcf594762b283cd2059204a2f43d9dedc3ef4d390f43349d122c6ffc6777343` |
| Agent | `financial-research-agent` |
| Declared source snapshot | `working-tree:a09195ed0fccf49b` (hash of app source/config/lock inputs, not proof of remote model revision) |
| Existing agent model | `openrouter/deepseek/deepseek-v4.1-flash` |
| Explicit extractor / formula judge | Same model, one extraction call and one formula-grading call; not an independent jury |
| Protocol | `everybench.findeepindicator.response-v1` |
| Run | `run_66c16f23df40` |
| Outcome | `succeeded`, one scored case, no execution/grading errors |
| Primary answer score | **1.0** |
| Formula component | **1.0** |
| Raw-data matching component | **0.0** — this component did not pass; the result does not establish complete raw-data correctness |
| Computed-series component | Not applicable (`null`) |
| Trace | `e52d7b2ea9c02970d14603d41748b8f5`, complete / producer-sealed `closed` |
| Genuine spans | Six: one request, two model calls, two `getCompanyMetric` calls and one `lookupCompany` call |
| Known tokens / total cost | Unknown (`null`); no all-inclusive cost cap is claimed |

The complete original dataset was hash-checked; the unmodified question was sent without references/rubrics or prior conversation history. FinDeepIndicator grades the submitted response; its trace is diagnostic. BigFinance trace-aware scoring was validated offline with an injected app and fake judge, **not** with a live BigFinance judge.

## Retained evidence

Private local acceptance directory:

`~/.everybench/acceptance/user-agent-20261026-004205/`

Persisted report:

`~/.everybench/acceptance/user-agent-20261026-004205/home/files/evaluations/run_66c16f23df40/report.json`

The directory also contains `acceptance.json`, non-secret CLI arguments, captured CLI output and the isolated EveryBench database/sample evidence. Reports used the redacted policy. The deliberately local collector allowed full visible agent content, with its own separate retention policy; no raw provider reasoning was requested as agent evidence. An exact-match scan of all 11 acceptance files found neither configured chat bearer token nor model API key. This is not a universal DLP guarantee.

The temporary receiver and financial-app processes launched for this check were stopped; pre-existing services were not stopped. To inspect the retained result without invoking a model, set `EVERYBENCH_HOME` to the directory's `home` and use `everybench show run_66c16f23df40` or start a loopback-only `everybench serve` against that home. Do not rerun `evaluate` without fresh authorization.

## Remaining limitations

Finance and Harbor extras require separate environments because their OpenAI SDK constraints conflict. HTTP transport currently supports the documented strict NDJSON protocol, not arbitrary HTTP response schemas. External callback authorization/completeness declarations remain trusted capabilities. Synchronous callable threads cannot be forcibly stopped; remote cancellation/deadlines are not billing guarantees. No PostgreSQL, Redis/cloud deployment, Docker/Harbor execution or authentication-browser acceptance was performed here. Previously identified SDK buffering/spool reliability and metadata privacy limitations were not comprehensively repaired by this evaluation-engine work.
