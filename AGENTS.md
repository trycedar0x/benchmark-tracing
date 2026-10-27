# Working on everybench

everybench is used by coding agents as well as people. Keep the Python SDK, HTTP API and CLI documented as distinct interfaces; do not assume a browser or interactive terminal.

## Start here

- Using the tool from another project: read [docs/agents.md](docs/agents.md).
- User-owned callable/HTTP evaluation: [SDK/CLI contracts](docs/evaluating-your-agent.md).
- Reusable consumer skills: [skills/README.md](skills/README.md). These teach tool use, not repository contribution.
- Human walkthrough: [docs/guide.md](docs/guide.md).
- The public SDK exports `EveryBench` for tracing/delivery and module-level `evaluate` / `evaluate_sync` for user-owned agent evaluation. User-owned-agent evaluation is the sole execution mode. The HTTP API queues operator-authorized target IDs; history/comparison remain available for legacy model runs.

## Setup and checks

Run from the repository root with Python 3.12+:

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check src tests examples skills
```

The base environment supports deterministic evaluation, historical imports/comparisons and tracing tests without provider keys, Docker or a web build. Install `--extra finance` for supported finance judge clients; `postgres` and `eee` retain database/export integrations. There are no Harbor or benchmark scheduling extras. Optional framework/Docker tests may skip. Cross-repository financial-app tests also skip unless the separately supplied compatible sibling app is built; that app is not delivered by this PR.

For frontend changes, also run `npm --prefix web ci && npm --prefix web run build`. Follow the existing shadcn/ui design tokens and components.

## Code map

| Area | Files |
| --- | --- |
| CLI and HTTP contracts | `src/everybench/cli.py`, `src/everybench/server.py` |
| Public tracing SDK and OTLP ingest | `src/everybench/sdk.py`, `src/everybench/otlp.py` |
| Queued agent evaluation and workers | `src/everybench/execution.py`, `jobs.py`, `evaluation/` |
| Experimental finance metadata and adapters | `src/everybench/finance/datasets.py`, `evaluation/benchmarks.py` |
| Paired comparison and trajectory diff | `src/everybench/compare.py`, `trace_diff.py` |
| Database and migrations | `src/everybench/db.py`, `src/everybench/migrations/` |
| Consumer skills and their scripts | `skills/everybench/`, `skills/everybench-sdk/` |

User-owned evaluation contracts, transports, trace sources and the shared engine live in `src/everybench/evaluation/`; `src/everybench/finance/` supplies experimental neutral finance adapters and grading helpers. The network API executes only operator-configured target IDs, never arbitrary caller-supplied URLs.

## Working rules

- Keep changes scoped to the request. Preserve unrelated work and use a separate worktree when requested.
- Run offline callable/tracing examples first. Do not call paid agents/evaluators without explicit authorization. Remote costs are not capped; never automatically replay failed paid work.
- Never use a real user's default database for tests. Set a throwaway `EVERYBENCH_HOME`; unset explicit database URLs, including legacy variables, before local smoke runs.
- Keep prompts, credentials and production traces out of commits and agent reports. Evaluation defaults to redacted; SDK and server ingest default to metadata. Historical full Inspect logs remain sensitive regardless of new content policy.
- Treat execution errors separately from wrong answers. Do not bypass compatibility checks or turn partial results into a pass.
- When changing an interface, update its documentation, affected skills/examples and tests together. Verify flags against `--help`, API payloads against `server.py`, and SDK methods against `sdk.py`.
- Skills must remain usable when copied outside this repository: keep required references and scripts inside each skill directory, use relative paths, and do not rely on repo-root files.

Shared/reverse-proxied serving requires authentication and TLS. Auth-off mode is null-workspace, single-user loopback only; CLI serving rejects unauthenticated non-loopback binds. Preserve exact workspace and independent agent/trace-secret authorization.
