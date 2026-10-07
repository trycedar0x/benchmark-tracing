# benchtrace

Run established benchmarks against any model, see a trace for every task, and compare runs with paired statistics. Open source, local-first, no account needed.

benchtrace reuses existing evaluation tools instead of rebuilding them: [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) runs the benchmarks and [inspect_evals](https://github.com/UKGovernmentBEIS/inspect_evals) supplies them. benchtrace adds the parts around them:

- **A versioned catalog.** Each entry pins one benchmark variant (task, arguments, grader). Runs on different variants are never silently compared.
- **Quotes and budget caps.** Run a small sample, get a token and cost estimate with a 95% range, approve it, and the run stops when it reaches its cap.
- **Traces for every task.** Model calls, tool calls, sandbox commands and grading become a span tree linked to the task's outcome.
- **Paired comparison.** Per-task regressions and improvements, a bootstrap confidence interval on the score difference, and McNemar's test. Execution errors are reported separately and never counted as wrong answers.
- **Model identity checks.** Every model call records which model actually answered. A run served by more than one model revision is blocked from strict comparison.

## Quick start (offline, no API keys)

```bash
git clone https://github.com/trycedar0x/benchmark-tracing && cd benchmark-tracing
uv sync

uv run benchtrace catalog
uv run benchtrace run toy-arith -m btmock/strong -m btmock/weak
uv run benchtrace compare <run-a> <run-b>
uv run benchtrace trace <run-b> arith-001
```

`btmock/*` models are deterministic mock models bundled for demos and tests. `toy-arith` and `toy-tools` are bundled benchmarks; `toy-tools` exercises tool calls.

New here? The [user guide](docs/guide.md) walks through reading comparisons, tracing your own agent and gating CI, and [`examples/`](examples) has runnable CLI, SDK, OTLP and HTTP API examples.

## Real models and benchmarks

```bash
uv sync --extra benchmarks            # installs inspect_evals
export OPENAI_API_KEY=...             # or any provider Inspect supports

uv run benchtrace quote create gsm8k -m openai/gpt-4o-mini -m anthropic/claude-haiku-4-5 --limit 200
uv run benchtrace quote approve <quote-id> --cap 5
uv run benchtrace run --quote <quote-id>
```

Model names follow [Inspect's provider format](https://inspect.aisi.org.uk/models.html). benchtrace ships no real provider prices because they change; add the models you use to `~/.benchtrace/pricing.yaml` (see [`src/benchtrace/pricing.yaml`](src/benchtrace/pricing.yaml)). Models without a price get token counts but cannot run with a budget cap.

Commands that call paid models ask for confirmation, or take `--yes`.

## Agent benchmarks with Harbor

Agent benchmarks (Terminal-Bench, SWE-bench Verified, Aider Polyglot) run through [Harbor](https://github.com/laude-institute/harbor) in Docker sandboxes. Each trial becomes a sample with its reward, tokens, cost and an ATIF trajectory converted into model and tool spans.

```bash
uv sync --extra harbor
uv run benchtrace run harbor-smoke -m oracle -m nop                 # bundled tasks, no API keys
uv run benchtrace run terminal-bench -m terminus-2:openai/gpt-4o --limit 10 --yes
```

Models take the form `agent:model`, or a bare model for the catalog's default agent. `oracle` (the reference solution) and `nop` (does nothing) are free. Harbor resolves registry datasets to their latest version. Every trial's task checksum is recorded, and runs whose task contents differ are blocked from direct comparison. With `--budget`, a run stops when Harbor-reported cost reaches the cap. Agent benchmarks pull large images; SWE-bench needs substantial disk space.

## Web UI and server

```bash
npm --prefix web ci && npm --prefix web run build   # builds the UI into the Python package
uv run benchtrace serve                              # http://127.0.0.1:8321, with 2 embedded workers
```

Or run everything with Postgres in Docker: `docker compose up --build`. The API, a worker and Postgres start, and the UI is at http://127.0.0.1:8321. Over the API, paid models need an approved quote (`BENCHTRACE_REQUIRE_QUOTE=0` disables this).

The UI is built with [shadcn/ui](https://ui.shadcn.com) (Nova preset). Browser smoke tests: `npx --prefix web playwright test` against a running server.

## Tracing your own agent

Send traces from your own code with the SDK, or point any OpenTelemetry OTLP/HTTP exporter at `/v1/traces`. Spans from OpenInference and GenAI-semantic-convention instrumentations (OpenAI, Anthropic, LangChain and others) are mapped to model, tool and agent steps.

```python
from benchtrace.sdk import Benchtrace

bt = Benchtrace("http://127.0.0.1:8321", content="metadata")   # or "redacted" / "full"
with bt.trace("answer-question", task_id="q-17"):
    with bt.span("call-model", kind="model", **{"gen_ai.request.model": "gpt-4o-mini"}) as span:
        bt.record(span, "output", "...")                         # kept only if the policy allows
print(bt.flush().receipts)                                       # per-trace receipt: expected vs received spans
```

Delivery is crash-safe. The content policy is applied before anything touches disk. Every batch is written to a local spool (fsync and atomic rename) before it is sent, and is deleted only after the server acknowledges it. Batches left behind by a crash are sent by the next client for the same endpoint. Each batch has a stable id, so a retry after a lost acknowledgement is not stored twice. A full spool refuses new spans and reports them in `bt.health()` instead of silently dropping them. When a `trace` block exits, it seals the trace with its span count. The trace shows as `closed` once every span has arrived, or `incomplete` if some are missing. A closed trace means everything the producer declared was received. It does not prove the instrumentation was complete.

The server stores ingested content according to `BENCHTRACE_INGEST_CONTENT` (default `metadata`). A client can ask for a stricter policy than the server's, but not a looser one.

## Imports and datasets

Import production traces from other tools, then turn interesting ones into reviewed evaluation tasks.

```bash
export LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=...           # or LANGSMITH_API_KEY, BRAINTRUST_API_KEY
uv run benchtrace import langfuse --max-traces 200 --rights own_data
uv run benchtrace import langsmith --project support-bot --rights own_data
uv run benchtrace import braintrust --project support-bot --rights own_data
uv run benchtrace import otlp spans.jsonl --rights own_data      # OTLP/JSON files
uv run benchtrace import inspect-log run.eval                   # an existing Inspect log becomes a run

uv run benchtrace dataset create "support failures"
uv run benchtrace dataset add <dataset> --run <run> --outcome incorrect --split dev
uv run benchtrace dataset review <item> --reference "..." --split test --approve
uv run benchtrace dataset export <dataset> -o tasks.jsonl       # approved items only, Inspect-compatible
```

Importing requires declaring your usage rights. Re-importing a trace replaces it instead of duplicating it. Dataset items start as drafts. The recorded output is kept as evidence and is never used as the reference answer automatically. An item can only be approved once a reviewer has set a reference answer, assigned a dev or held-out test split, and confirmed usage rights. The same flow is in the web UI under Imports and Datasets.

## Teams: authentication and workspaces

Authentication is off by default (single-user local mode, bound to localhost). For a shared server:

```bash
export BENCHTRACE_AUTH=1 BENCHTRACE_SECRET_KEY="$(openssl rand -hex 32)"
uv run benchtrace admin create-user you@example.com --workspace main      # prompts for a password
uv run benchtrace admin create-key --workspace main --name ci             # prints a bt_... key once
uv run benchtrace admin set-secret OPENAI_API_KEY --workspace main        # encrypted provider key
uv run benchtrace serve --host 0.0.0.0
```

- Every run, trace, quote, import and dataset belongs to one workspace. Members only see their own workspaces.
- Roles: owners manage secrets, members create runs and keys, and viewers are read-only.
- The browser uses an HttpOnly session cookie. Scripts, the SDK and OTLP exporters send `Authorization: Bearer bt_...`. Only key hashes are stored.
- Provider keys and import credentials are stored encrypted (Fernet, keyed from `BENCHTRACE_SECRET_KEY`). They are passed only to that workspace's run processes and imports, and are never returned by the API.
- Put TLS in front of the server before exposing it beyond your machine.

The database schema is managed with Alembic migrations, applied automatically on startup.

## Commands

| Command | What it does |
| --- | --- |
| `catalog [BENCHMARK]` | List benchmark variants, or show one |
| `quote create BENCH -m MODEL...` | Run a few samples per model and estimate full-run tokens and cost |
| `quote approve ID --cap USD` | Approve a quote with an optional budget cap |
| `run BENCH -m MODEL...` | Run with live progress; `--limit`, `--epochs`, `--budget`, `--content` |
| `run --quote ID` | Run an approved quote |
| `runs`, `show RUN` | List runs; show a run's summary, versions and samples |
| `trace RUN SAMPLE` | Print the span tree for one task |
| `compare A B` | Paired comparison; `--json` for every row |
| `diff A B SAMPLE` | Where the two runs' trajectories for one sample diverged |
| `cancel RUN` | Stop a running run gracefully |
| `import SOURCE`, `dataset ...` | Import traces; draft, review and export datasets |
| `admin ...` | Create users, workspaces, API keys and encrypted secrets |
| `serve`, `worker` | Start the API and web UI; run queued jobs in a separate process |
| `export RUN` | JSON export, or `--format eee` for [Every Eval Ever](https://github.com/evaleval/every_eval_ever) (needs `--extra eee`; real providers only) |

## How it works

Each run executes in its own child process. Inspect hooks stream every finished sample into the database as it completes, with its result and spans. Budget stops and cancellation send the child a SIGINT, which Inspect handles as a graceful cancel and writes a partial log. Samples interrupted that way are marked `cancelled`, not `error`. With a budget, at most four samples run at once, so a stop overshoots by at most a few in-flight calls.

State lives in `~/.benchtrace` (override with `BENCHTRACE_HOME`): a SQLite database and the original Inspect logs, which open in [Inspect View](https://inspect.aisi.org.uk/log-viewer.html). Set `BENCHTRACE_DATABASE_URL` to use Postgres.

### Content policy

`--content full` (default for benchmark runs) stores prompts and outputs. `redacted` stores them after best-effort secret redaction. `metadata` stores only timings, token counts, scores and structure. Redaction catches common credential formats and sensitive field names; it cannot catch every secret in free text. Original Inspect logs are kept locally in full regardless of policy.

## Status

Early but complete for its first scope: CLI, API server and job queue, web UI, traces and trace diff, OTLP ingest and SDK, imports and dataset drafts, Harbor agent benchmarks, and workspaces with authentication. Not yet: remote execution backends (Modal, Daytona, Kubernetes), custom HTTPS scorers, and configuration tuning.

## Development

```bash
uv sync --all-extras
uv run pytest                                   # SQLite
BENCHTRACE_TEST_POSTGRES=postgresql+psycopg://postgres:pw@localhost/postgres uv run pytest   # Postgres
uv run ruff check src tests
npm --prefix web ci && npm --prefix web run build
```

## License

Apache-2.0. Benchmark datasets keep their own licenses, listed in the catalog.
