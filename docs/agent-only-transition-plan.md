# Agent-only evaluation transition

## Goal and authorized scope

Replace the old model/Inspect/Harbor scheduling product with evaluation of user-owned agents. Remove the old CLI `run` and `quote` commands, model scheduling and quote HTTP routes, bundled solver/Harbor execution paths, obsolete catalog entries, optional dependencies, examples and tests specific to those paths. Replace the web New run flow rather than adding a second mode. Keep the Python callable/HTTP evaluation SDK, tracing SDK and OTLP receiver, imports, datasets, reports, cancellation and paired comparisons.

Historical runs, samples, evidence and traces remain readable. Retain compatibility schema and import/conversion code where needed to read existing data; do not delete user data, drop historical columns/tables or attempt to reinterpret legacy runs as external-agent runs. Finance remains explicitly experimental: FinDeepIndicator grades submitted responses; BigFinance supports only the public 50-item subset and requires genuine complete visible trace evidence. Do not claim official leaderboard parity.

## Baseline and isolation

Start from merged `origin/main` at `cb5ba11` (PR #12). The original checkout at `/Users/jacobwang/codebase/personal/benchmark-tracing` contains unrelated uncommitted work and is not an implementation workspace. The integration branch is `feat/agent-only-evaluation` in `/Users/jacobwang/codebase/personal/benchmark-tracing-agent-only`. Each component writer gets a separate worktree from the plan commit, an exclusive source boundary and a committed handoff. Only the integration owner combines component commits. No pushes, deployments or paid benchmark/provider calls are part of implementation.

## Shared HTTP and queue contract

- `GET /api/catalog` lists only the two implemented user-agent finance adapters, with clear experimental status, counts, grading requirements and license metadata. It must be discoverable by the web UI without loading/downloading datasets or invoking providers.
- `GET /api/evaluation-targets` lists the caller's operator-configured agent targets and whether each has an authorized trace source. It exposes a public ID, name/version and safe endpoint origin, not secrets or capability-bearing endpoint paths.
- `POST /api/runs` now submits one user-agent evaluation and returns one queued run object, not an array of model runs. Request fields: `benchmark`, `target_id`, `judge_models`, optional `extractor_model`, `sample_ids`, positive bounded `limit`, positive bounded `timeout`, nonnegative bounded `trace_timeout`, positive bounded `grading_timeout`, `content_policy` (`metadata`, `redacted`, `full`; default `redacted`), and explicit `allow_paid: true`. No caller-supplied URL, Python import path, filesystem path, credential/environment variable name, model list for an agent solver, budget/quote or epochs field is accepted. Reject unknown fields and incompatible grading configuration before enqueueing.
- Only preconfigured targets are runnable over the network API. `EVERYBENCH_TARGETS_FILE` points to operator-owned JSON configuration (not an uploaded/publicly served file): a list of target profiles with `id`, `name`, `version`, `url`, optional `workspace_id`, `token_secret`, and optional `trace` containing `type` (`store` or `http`), `url` for HTTP, and `token_secret`. Optional pinned local `data_path` is operator-only. Profile credentials reference workspace secrets; raw credentials never enter requests, run manifests, job rows, diagnostics or API responses. Local auth-off profiles use `workspace_id: null`; authenticated workspaces must match exactly. Do not expose an arbitrary-URL proxy or let a member register new network capabilities.
- Validate URL structure with existing transports, prohibit redirects and preserve their strict NDJSON request/response contract. Agent and trace authorization remain independent. A trusted operator configuration is the network-capability boundary, including intentional loopback deployments.
- Queue the job and run atomically. A worker executes the same shared evaluation engine, in a separate subprocess for workspace credential isolation, binding the existing queued run ID rather than creating a duplicate run. Bind submission to the chosen target configuration fingerprint and fail closed if that capability changes before execution. Resolve secret values only for the authorized workspace at execution.
- Do not replay stale/failed paid work automatically. Queued cancellation must prevent invocation; running cancellation must persist partial results and propagate through the engine. Preflight/export/grading failures must be distinguishable from incorrect answers and must never expose raw provider errors or secrets.
- Existing read, cancel, comparison, evidence and tracing endpoints remain available. Remove `/api/quotes` and `/api/models` scheduling surfaces. Preserve legacy history rendering, not legacy execution.

## Web behavior and design system

Reuse existing shadcn/ui primitives, Tailwind utilities and CSS theme tokens for colors, typography, spacing, radius and elevation. Do not introduce a second stylesheet or framework. New run is a single agent-evaluation form: benchmark, configured agent target, explicit judge models, FinDeepIndicator extractor, sample selection/limit, timeouts, content policy and an unchecked consent checkbox. BigFinance visibly requires an available trace source. Show the strict HTTP protocol requirement and that remote/evaluator costs are not capped or quoted. Loading, no-target setup guidance, failures and viewer permissions must be handled. On successful submission navigate to the new run's existing detail page. Replace the legacy catalog description and empty-run instructions.

## Implementation topology and lane board

This is multi-seam work: the queued backend contract, the browser form, and legacy runtime/package removal have independent tests and exclusive ownership. Parallel writers must not share a cwd.

| Lane | Workspace suffix | Owned files/contracts | Gate and durable handoff | Why independent |
| --- | --- | --- | --- | --- |
| Backend | `benchmark-tracing-agent-backend` | `src/everybench/server.py`, `jobs.py`, `execution.py`, new evaluation submission/worker modules, `evaluation/engine.py`, `evaluation/benchmarks.py` catalog metadata only, backend submission/security/job tests, backend-contract documentation in new files | API/worker/security/offline engine tests; commit and report schema/commands | Implements shared queued HTTP execution; no web or legacy CLI deletion |
| Web | `benchmark-tracing-agent-web` | `web/src/pages/NewRun.tsx`, `Catalog.tsx`, `Runs.tsx`, `web/src/api.ts`, relevant `web/e2e` tests; other web-only changes if required | Typecheck/build and mocked API UI tests; commit and report | Consumes the fixed HTTP contract; no Python edits |
| Legacy removal | `benchmark-tracing-agent-cleanup` | `cli.py`, legacy catalog/service/child/backends/Harbor and solver modules, optional-dependency configuration/lock, obsolete examples/skills/docs, compatibility import/dataset conversion adjustments and legacy tests; excludes backend/web ownership | CLI help, retained import/history tests, lint and full offline suite where possible; commit and list intentional removals | Removes old execution surfaces without rewriting new queue or web |
| Integration | `benchmark-tracing-agent-only` | Combine committed handoffs; resolve cross-boundary imports, contract mismatches and regression fixes only; reconcile top-level project instructions and docs with actual delivery | Full offline Python suite, Ruff, npm clean install/build and relevant UI tests; final review | Starts only after all component writers finish |
| Review | integration tree, read-only | Independent security/lifecycle and product/regression review | Evidence-backed findings with exact paths and remediation; no source writes | Fresh review of the integrated result, not a duplicate implementation |

Each writer may adjust tests tightly coupled to its owned files, but must not delete unrelated coverage merely to obtain a pass. Shared compatibility symbols needed by retained code should be reported to integration rather than stubbed with fake success. Legacy-only tests can be removed with the retired feature; new user-agent tests must cover the replacement. Update docs, consumer skills and examples together; copied skills must remain self-contained.

## Validation and acceptance

1. Verify `everybench --help` has evaluate/tracing/history/import/admin/server commands and no legacy `run`/`quote`; verify documented flags against help.
2. Verify public catalog and New run expose FinDeepIndicator and BigFinance with correct requirements; no legacy model, Harbor or quote choice remains.
3. Exercise authenticated workspace isolation, viewer rejection, unknown target/benchmark, arbitrary URL/extra-field rejection, missing consent, invalid evaluator configuration, missing BigFinance trace source and independent target/trace secrets. Assert rejected requests never invoke providers or agents.
4. Run an offline loopback NDJSON agent plus synthetic pinned dataset/mock judge through POST → queued job → shared engine → existing report/API. Assert one run identity, genuine question-only inputs, progress/result persistence and no secret leakage. Cover worker/preflight failure and queued/running cancellation without replay.
5. Retain tests for tracing, imports, artifacts, history and paired comparisons, including old model-run fixtures as read-only historical compatibility where appropriate.
6. With throwaway `EVERYBENCH_HOME` and current/legacy explicit database URLs unset, run `uv sync --frozen`, `uv run pytest -q`, and `uv run ruff check src tests examples skills`. Run `npm --prefix web ci && npm --prefix web run build`, plus relevant Playwright tests. Record skips and environment limitations rather than hiding them.
7. Obtain fresh read-only review of security/job lifecycle and UI/API migration. Fix concrete regressions and rerun affected gates before declaring completion. Do not push or publish without separate authorization.

## Progress

- Plan recorded before implementation. Component worktrees and handoffs pending.
