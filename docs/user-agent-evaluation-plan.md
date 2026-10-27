# User-owned agent evaluation: implementation plan

> Historical record: runner/catalog details describe the earlier implementation, not current execution interfaces. Use [current agent evaluation contracts](evaluating-your-agent.md). This record grants no new spend authorization.

Status: implemented and accepted for the bounded local integration. CLI/SDK, neutral evidence/scoring, persistence/comparison and financial-app observability are complete; offline gates and the single authorized live case are recorded in [the acceptance record](user-agent-evaluation-acceptance.md). Finance adapters remain experimental, not official leaderboard parity.

## Product contract

Users bring their own agent, invoked by an explicit Python callable or an authenticated HTTP endpoint. EveryBench supplies benchmark cases, calls the agent without changing its prompts/tools/decision process, associates the real execution trace, applies the benchmark scorer, and stores a report that can be inspected and compared.

Users may send traces through our SDK/OTLP receiver or authorize access to an existing trace source. Agent invocation and trace acquisition are separate interfaces. Inspect/Harbor remain optional compatibility runners, not mandatory execution environments for user agents. No Docker or arbitrary-code tool is required for HTTP/callable targets.

## Intended flow

1. Configure the target agent, benchmark, explicit evaluator models, trace source, limits and storage policy.
2. Validate required capabilities and credentials without model calls; create an evaluation run describing the actual target/version, not a fictitious model identity.
3. Load immutable, licensed benchmark data. Keep references and rubrics out of agent inputs.
4. For each case/attempt, create a fresh invocation context; call the user's agent and collect an explicitly completed answer. Do not automatically retry paid/stateful calls.
5. Associate the trace using trusted invocation context or the returned trace ID. Wait for bounded delivery/completion, or explicitly record missing/incomplete/withheld evidence.
6. Grade the submitted answer and, when required, the user's actual trace. Never substitute the evaluator's or an internal baseline's actions as agent evidence.
7. Store outcome, score components, explanations, evidence, timing and known usage. Unknown remote costs stay unknown. Produce a machine-readable report and retain existing inspection/comparison surfaces.

## Scope and milestones

- [x] Define transport-independent case, agent result, trace evidence and evaluation report contracts.
- [x] Add SDK evaluation APIs and a thin CLI sharing one engine, with callable and strict authenticated HTTP/NDJSON adapters, cancellation/timeouts, fresh conversations and no implicit retries.
- [x] Persist external-agent identity/version/public configuration while preserving legacy runs and legitimate multi-model agents.
- [x] Add independently configured local store/HTTP and authorized callback trace adapters with invocation binding, ownership, completeness and withheld-evidence checks.
- [x] Make the two experimental finance adapters consume neutral response/execution evidence, retaining legacy Inspect wrappers without constructing an internal research agent.
- [x] Instrument actual financial-app requests, model calls and tools using optional OTel/OTLP, preserving business behavior, auth, NDJSON, limits, history, cancellation and hidden-reasoning protections.
- [x] Verify actual local app HTTP → OTLP → scoring → persisted report/API using injected models, SEC fixtures and fake judges; cover partial/missing/withheld evidence, privacy and scoring integrity.
- [x] Consume the current session's one-case live authorization: original FinDeepIndicator sample `1799`, run `run_66c16f23df40`, complete six-span trace; answer/formula components passed, raw-data matching did not. No deployment or retry. Further runs require fresh authorization.
- [x] Update usage/examples and acceptance evidence; validate Python/app/web tests, type/build, package/lock and fresh reviews. See [acceptance and limitations](user-agent-evaluation-acceptance.md).

## Financial app integration facts

The app exposes bearer-protected `POST /api/chat` with strict `{message, conversationId?}` input and NDJSON `conversation`, `delta`, `done/error` output. Optional OTel instrumentation now exports actual requests, model calls and tools; default metadata/redacted modes withhold bodies, while explicit full content remains bounded and scrubbed. Correlation uses headers/context, not additional JSON body fields. Conversation history is not a full trace, and partial text is not a completed answer.

The agent is deliberately limited to SEC company lookup, recent filing metadata and a small allowlisted US-GAAP observation set. Do not add web/Python/full-filing capabilities merely to improve benchmark scores. Use a relevant declared case/subset for the live integration check and preserve benchmark IDs/references.

PR scope: the operator explicitly chose to publish only EveryBench. The sibling financial app's local observability changes are not part of this repository or this PR. Cross-repository tests skip without that separately supplied built app; its live/offline results are local acceptance evidence, not a reproducibility claim for a clean PR checkout.

## Safety and acceptance

- Existing working-tree changes are preserved. No commits, deployment, deletion of user resources or credential printing.
- Trace credentials and target credentials remain separate. Do not forward authorization to redirects or arbitrary URLs. Do not accept arbitrary endpoint execution through a shared public API without a separate trusted-target security policy.
- Trace data must match the invocation/workspace and include genuinely captured agent events; the final answer, judging traffic or fabricated spans cannot replace missing internal evidence.
- Missing trace and withheld content are explicit evaluator states, not invented evidence or silent valid zero scores. Storage policy is applied before new persistent evidence is written.
- Evaluators receive sensitive evidence only with explicit provider/configuration consent. Original runner logs and externally hosted traces have their own retention policies.
- No claim of an all-inclusive dollar cap for user-owned remote execution. Explicit case/time/attempt limits are not provider billing guarantees.
- CLI and SDK must produce the same result schema. Both finance tasks must run through user-agent adapters without Inspect's built-in research solver or Docker.
- Legacy benchmark tests continue passing. The financial app's existing tests/typecheck/build continue passing, with new offline tracing/protocol tests.

## Live authorization

This plan is not authorization for future executions. In the current interactive session, the operator answered the explicit live-test permission question with “允许实测” after being offered one real agent invocation and that case’s necessary judge calls, without deployment or automatic retries. That session-specific approval permits only one bounded relevant case for this acceptance check. Offline tests remain the default. Any later session, additional live case, rerun, provider change or wider paid work requires fresh operator authorization; never infer permission from this document or from a code review.

This session's single live invocation has now been consumed by run `run_66c16f23df40` (original case `1799`) and its necessary grading. It must not be repeated under this authorization. The temporary processes were stopped; report/evidence paths and component-level results are in the acceptance record.

## Delegated implementation lanes

The operator explicitly requested subagent implementation. This is multi-seam work; components have isolated snapshots and exclusive owners. No child may access live credentials, run paid models, deploy, commit or spawn more agents. Parent owns live-test authorization and final acceptance.

| Lane | Repository / source seam | Isolation and authority | Next gate / handoff | Why independent |
| --- | --- | --- | --- | --- |
| Transport/evidence | EveryBench: `evaluation/agents.py`, `evaluation/traces.py`, `evaluation/evidence.py`, neutral BigFinance scorer adapter, focused tests | Dedicated `transport` snapshot; shared `evaluation/types.py` is read-only | Offline protocol/security/provider tests; exact changed-file handoff | Implements frozen invocation/trace contracts without owning evaluation lifecycle or database |
| Core SDK/CLI | EveryBench: evaluation engine/benchmark adapters, persistent target/run compatibility, SDK exports, CLI and focused tests/docs | Dedicated `core` snapshot; starts after transport handoff; must not edit transport-owned modules/contracts | Legacy + engine/migration/CLI tests; exact changed-file handoff | Implements the lifecycle against completed transport contracts |
| Financial app | `financial-research-app`: optional OTel/OTLP observability, model/tool hooks, config, tests | Dedicated `financial-app` snapshot; no model/provider/prompt/tool-capability changes | Typecheck/build/offline app tests; exact changed-file handoff | Separate repository, same fixed OTLP/correlation contract |
| Integration | Both actual working copies, after all component handoffs | Sole integration writer; copy only declared owned changes, preserve all pre-existing edits | Combined regression/type/build tests and offline real-HTTP/OTLP scoring test | Integration only; no parallel actual-checkout writers |
| Review | Integrated source in both repositories | Fresh-context read-only reviewers | Evidence-backed correctness/security findings | No source mutation; parent triages findings |

Snapshots live outside the repositories under the current session’s `everybench-agent-lanes-*` temporary directory; baseline hashes and `lanes.json` identify origins. Managed `worktree:true` is intentionally not used because the source tree contains pre-existing uncommitted work. No commit is created merely to enable delegation.

Shared wire contract: HTTP agent requests use standard `traceparent` plus `x-everybench-run-id`, `x-everybench-sample-id`, `x-everybench-attempt`; strict chat JSON remains unchanged. An instrumented app returns `x-everybench-trace-id`. Root OTLP attributes use `everybench.run_id`, `everybench.sample_id`, `everybench.attempt`. Agent tools/models must emit actual captured events, then export and optionally seal `/api/traces/<trace-id>/seal` with the producer’s own expected span count; delivery failures remain incomplete. Tokens/cookies/secrets are never copied into attributes. Transport and evaluator must not invent assistant/tool events from the final answer.

The first integrated offline gate passed (311 Python tests, 39 app tests and six browser tests; optional skips recorded in the managed handoff), but fresh engine/tracing review blocked acceptance on concrete evidence/privacy/scoring defects. Live testing remains paused. The bounded fix wave is partitioned by seam: an EveryBench evidence-integrity owner (`evaluation/evidence.py`, `evaluation/traces.py`, neutral BigFinance scorer and focused tests; narrow engine revalidation glue only), an independent financial-app observability owner (attribute scrubbing, explicit withheld/truncated-body markers, canonical captured tool calls and tests), then a serial EveryBench public-identity/summary owner (safe numeric token configuration, external mean-score display, target-aware multi-model labeling). Writers use the two actual repositories only after earlier component writers are terminal, one writer per repository at a time. A separate integration-only validation owner follows completed fix handoffs, then fresh read-only reviews. Existing staged/working changes, the frozen shared types and parent-owned plan remain protected.

Cross-repo withholding contract for this fix wave: use non-content metadata `everybench.evidence.withheld=true` and `everybench.evidence.withheld_keys` (bounded canonical body-key names) when producer capture is size-limited, unserializable or otherwise partial. No omitted body values belong in metadata. Both trace validation and direct formatting must treat this as explicit withheld evidence rather than certify unrestricted visible completeness. A successful execution seal proves delivered span completion, not complete disclosure of bounded/withheld content.

Final outcome: accepted review findings were fixed and independently re-reviewed with no remaining findings. The parent reran the exact final Python source: 496 passed / 7 optional skips, lint/whitespace/lock clean. App gates passed 50 tests and browser gates 11 tests / 1 auth skip. The authorized live FinDeepIndicator case succeeded with complete real evidence, numerical-answer/formula scores 1 and raw-data match 0; it is not a representative benchmark score. See the acceptance record for private report paths, review limitations and separate finance/Harbor environments.
