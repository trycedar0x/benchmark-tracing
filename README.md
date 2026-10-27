# everybench

Evaluate the agent you already run. EveryBench loads cases, invokes your callable or strict HTTP service, collects actual answers and execution evidence, grades results and compares changes. User-owned-agent evaluation is the sole execution mode; historical model/Harbor runs, traces, imports, datasets and comparisons remain readable.

## Start locally

Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --frozen
uv run everybench catalog --json
uv run everybench serve                       # loopback API and web UI
```

For an offline evaluation fixture, use a throwaway home and unset explicit database URLs:

```bash
unset EVERYBENCH_DATABASE_URL EVERYEVAL_DATABASE_URL BENCHTRACE_DATABASE_URL
export EVERYBENCH_HOME="$(mktemp -d)"
uv run python examples/evaluate_callable.py --yes
```

This checks integration, not agent quality. No provider keys or Docker are needed. Real agent/evaluator calls require explicit operator approval and have no all-inclusive cost cap or automatic retry.

## Three distinct interfaces

- **Python SDK:** module-level `everybench.sdk.evaluate` / `evaluate_sync` evaluate existing callables/HTTP agents. `EveryBench` supplies tracing/delivery, not instance evaluation methods.
- **CLI:** `evaluate` shares that engine and accepts explicit operator-owned HTTP endpoints. History, trace, import, dataset and comparison commands use the local database, not `EVERYBENCH_URL`.
- **HTTP API and web:** queue one evaluation against an operator-configured `target_id`. Callers cannot supply arbitrary URLs, code paths, credentials or workspace overrides. Discover `/api/catalog`, `/api/evaluation-targets` and the installed `/openapi.json` contract.

Read [the user guide](docs/guide.md), [SDK/CLI contracts](docs/evaluating-your-agent.md), [coding-agent guide](docs/agents.md), [portable skills](skills/README.md) and [examples](examples/README.md).

## Experimental finance catalog

Only **FinDeepIndicator** (3,350 public cases) and **BigFinance public-50** are implemented named adapters. Install `everybench[finance]` for supported judge clients. Choose evaluator models explicitly: FinDeepIndicator requires one judge and an extractor; BigFinance requires distinct judge models and genuine complete visible agent trace evidence. References/rubrics never enter agent inputs. These versioned adaptations are not official leaderboard parity; see [finance requirements and licenses](docs/finance-evaluations.md).

## Traces, history and comparisons

Use the tracing SDK or OpenTelemetry OTLP/HTTP `/v1/traces` to instrument actual operations. SDK/ingest default to metadata; evaluation defaults to redacted. A closed delivery seal verifies declared span receipt, not complete instrumentation, answer correctness or passing evidence. Missing/withheld evidence and execution/grading errors have null scores, not incorrect zeroes. Remote costs remain unknown.

`everybench runs`, `show RUN`, `trace RUN SAMPLE`, `compare A B --json`, `diff A B SAMPLE` and `export RUN` inspect persisted results. Original historical schemas and logs are retained; `run`/`quote`, model scheduling and Harbor execution are removed. Inspect remains for grading and importing existing logs. Imported full logs and exports can contain sensitive material regardless of new storage policy.

Imports support Langfuse, LangSmith, Braintrust, OTLP files and existing Inspect logs. Dataset drafts require reviewed references, rights and dev/test splits before export. Historical output is evidence, never an automatic gold answer.

## Shared deployments

```bash
export EVERYBENCH_AUTH=1 EVERYBENCH_SECRET_KEY="$(openssl rand -hex 32)"
uv run everybench admin create-user you@example.com --workspace main
uv run everybench admin set-secret OPENAI_API_KEY --workspace main
uv run everybench serve --host 0.0.0.0
```

Enable authentication and TLS for shared/reverse-proxied deployments. Auth-off is single-user loopback only; CLI serving rejects other binds. Operators configure `EVERYBENCH_TARGETS_FILE`; submissions use only authorized profile IDs scoped exactly to the principal's workspace. Secrets are workspace-encrypted, independently authorized for agent and trace access, and never accepted in submission bodies. Viewers are read-only. See [queued API boundaries](docs/evaluating-your-agent.md#queued-http-api-and-web-submissions) and the [operator profile and worker configuration guide](docs/backend-evaluation-contract.md).

State defaults to `~/.everybench`; set `EVERYBENCH_HOME` or `EVERYBENCH_DATABASE_URL` for another database (Postgres via the `postgres` extra). Legacy `EVERYEVAL_*`/`BENCHTRACE_*` settings, existing data folders, trace attributes and keys remain compatible.

## Development

```bash
uv sync --frozen
# Use a throwaway home and unset current/legacy database URLs before tests.
uv run pytest -q
uv run ruff check src tests examples skills
npm --prefix web ci && npm --prefix web run build
```

Optional extras: `finance` for judge clients, `postgres` for Postgres, `eee` for historical Inspect-log conversion. No benchmark-runner or Harbor extra remains. Read [AGENTS.md](AGENTS.md) before contributing. Historical [acceptance evidence](docs/user-agent-evaluation-acceptance.md) records bounded earlier checks, not authorization for new paid execution.

## License

Apache-2.0. Dataset licenses and attribution remain distinct.
