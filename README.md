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

## Web UI and server

```bash
npm --prefix web ci && npm --prefix web run build   # builds the UI into the Python package
uv run benchtrace serve                              # http://127.0.0.1:8321, with 2 embedded workers
```

Or run everything with Postgres in Docker: `docker compose up --build`. The API, a worker and Postgres start, and the UI is at http://127.0.0.1:8321. Over the API, paid models need an approved quote (`BENCHTRACE_REQUIRE_QUOTE=0` disables this).

The UI is built with [shadcn/ui](https://ui.shadcn.com) (Nova preset). Browser smoke tests: `npx --prefix web playwright test` against a running server.

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
| `cancel RUN` | Stop a running run gracefully |
| `serve`, `worker` | Start the API and web UI; run queued jobs in a separate process |
| `export RUN` | JSON export, or `--format eee` for [Every Eval Ever](https://github.com/evaleval/every_eval_ever) (needs `--extra eee`; real providers only) |

## How it works

Each run executes in its own child process. Inspect hooks stream every finished sample into the database as it completes, with its result and spans. Budget stops and cancellation send the child a SIGINT, which Inspect handles as a graceful cancel and writes a partial log. Samples interrupted that way are marked `cancelled`, not `error`. With a budget, at most four samples run at once, so a stop overshoots by at most a few in-flight calls.

State lives in `~/.benchtrace` (override with `BENCHTRACE_HOME`): a SQLite database and the original Inspect logs, which open in [Inspect View](https://inspect.aisi.org.uk/log-viewer.html). Set `BENCHTRACE_DATABASE_URL` to use Postgres.

### Content policy

`--content full` (default for benchmark runs) stores prompts and outputs. `redacted` stores them after best-effort secret redaction. `metadata` stores only timings, token counts, scores and structure. Redaction catches common credential formats and sensitive field names; it cannot catch every secret in free text. Original Inspect logs are kept locally in full regardless of policy.

## Status

Early. The CLI, API server, job queue and web UI work. In progress: OTLP ingest and a Python SDK for your own agents, imports from Langfuse, LangSmith and Braintrust, and trace diffs.

## Development

```bash
uv sync --extra benchmarks
uv run pytest
uv run ruff check src tests
```

## License

Apache-2.0. Benchmark datasets keep their own licenses, listed in the catalog.
