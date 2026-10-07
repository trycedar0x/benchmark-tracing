# benchtrace user guide

This guide walks you through benchtrace from a first offline run to tracing your own agent and gating CI on regressions. Every command here runs without API keys unless a section says otherwise. For a reference of every command and option, see the [README](../README.md) and `benchtrace --help`.

Runnable versions of the examples are in [`examples/`](../examples).

## Contents

1. [Install](#1-install)
2. [Your first comparison in five minutes](#2-your-first-comparison-in-five-minutes)
3. [Reading the results](#3-reading-the-results)
4. [Finding out why a sample changed](#4-finding-out-why-a-sample-changed)
5. [Running real models without surprise bills](#5-running-real-models-without-surprise-bills)
6. [Tracing your own agent with the Python SDK](#6-tracing-your-own-agent-with-the-python-sdk)
7. [Sending traces from other languages](#7-sending-traces-from-other-languages)
8. [Using the HTTP API (CI regression gate)](#8-using-the-http-api-ci-regression-gate)
9. [Turning failures into a dataset](#9-turning-failures-into-a-dataset)
10. [Troubleshooting](#10-troubleshooting)

## Key ideas

**Benchmark variant.** A catalog entry such as `toy-arith@1` pins the task, its arguments and its grader. Runs on different variants are never compared silently.

**Run.** One model on one benchmark variant. Each run has an id like `run_0687a9023c2a`.

**Sample.** One task inside a run, such as `arith-001`. Its outcome is `correct`, `incorrect`, `error` or `cancelled`. Errors are execution failures, not wrong answers, and are kept out of scores.

**Trace.** The tree of steps (spans) behind one sample: model calls, tool calls, sandbox commands and grading. Traces can also come from your own app through the SDK or OpenTelemetry.

**Mock models.** `btmock/strong`, `btmock/weak`, `btmock/flaky` (sometimes errors) and `btmock/drifty` (changes revision mid run) are deterministic fakes for learning and testing. Their prices are fictional.

## 1. Install

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/trycedar0x/benchmark-tracing && cd benchmark-tracing
uv sync
uv run benchtrace --help
```

Everything benchtrace stores (a SQLite database and the original Inspect logs) goes in `~/.benchtrace`. To experiment without touching that, point `BENCHTRACE_HOME` somewhere else:

```bash
export BENCHTRACE_HOME=$(mktemp -d)
```

## 2. Your first comparison in five minutes

See what you can run:

```bash
uv run benchtrace catalog
uv run benchtrace catalog toy-arith      # details for one benchmark
```

Run two models on the same benchmark. They run in parallel with live progress:

```bash
uv run benchtrace run toy-arith -m btmock/strong -m btmock/weak
```

```text
│ run_0687a9023c2a │ btmock/strong │ succeeded │ ████████████████████ 40/40 │ 97.5%    │ 0      │ 913    │ $0.0047 │
│ run_fc637fac9091 │ btmock/weak   │ succeeded │ ████████████████████ 40/40 │ 62.5%    │ 0      │ 913    │ $0.0005 │
Compare: benchtrace compare run_0687a9023c2a run_fc637fac9091
```

The last line is the next command to run. Your run ids will differ.

```bash
uv run benchtrace compare run_0687a9023c2a run_fc637fac9091
```

Lost the ids? `uv run benchtrace runs` lists recent runs.

## 3. Reading the results

```text
                toy-arith@1: A=btmock/strong vs B=btmock/weak
┃                   ┃ A     ┃ B     ┃ Δ (B−A)   ┃ 95% CI         ┃ McNemar p ┃
│ Score (40 paired) │ 97.5% │ 62.5% │ -35.0 pts │ -52.5 to -17.5 │ 0.000519  │
Improvements: 1   Regressions: 15   Errors: A only 0, B only 0, both 0 (excluded from scores)
```

How to read each part:

**Score (40 paired).** Both runs answered the same 40 samples, so they are compared sample by sample, not just as two averages. Pairing removes the noise of "this run happened to get easier questions".

**Δ (B−A).** B's score minus A's, in percentage points. Negative means B is worse.

**95% CI.** A bootstrap confidence interval for Δ. If the whole interval is below zero, B is very likely worse; if it straddles zero, you cannot tell yet and need more samples.

**McNemar p.** A test that looks only at samples where the two runs disagreed. A small value (below 0.05 is the usual bar) means the disagreements lean one way more than chance would explain.

**Improvements / Regressions.** Samples that went from wrong to right, and from right to wrong. The table below lists them with their trace ids.

**Errors.** Samples that crashed rather than answered. They are reported separately and excluded from the score, so a flaky provider does not look like a dumb model.

The two statistics can disagree on small samples. Comparing `btmock/strong` with `btmock/flaky` on 20 samples gave a CI of `-47.1 to -5.9` (below zero) but `McNemar p = 0.125` (not significant), because only 4 samples disagreed. When that happens, run more samples before deciding.

Every row, in machine-readable form:

```bash
uv run benchtrace compare <run-a> <run-b> --json
```

## 4. Finding out why a sample changed

Start from a regression in the compare table, for example `arith-001`. Show the samples a run got wrong:

```bash
uv run benchtrace show <run-b> --outcome incorrect --samples 5
```

Print the span tree of one sample:

```bash
uv run benchtrace trace <run-b> tools-006
```

```text
trace 8ab06fd5db7e53796a8a8d92ab7a3d6a · inspect
└── agent sample tools-006 1.07s “The answer is -9066”
    ├── span solvers 885ms
    │   └── solver generate 880ms
    │       ├── model btmock/weak 718ms 29→15 tok “tool call for tool calculator”
    │       ├── tool calculator 1ms “-9069”
    │       └── model btmock/weak 35ms 38→12 tok “The answer is -9066”
    └── span scorers 1ms
        └── scorer match 1ms score=0.0 “-9066”
```

Here the calculator returned `-9069` but the model answered `-9066`: the tool was right and the model misread it.

To see exactly where two runs went different ways on the same sample:

```bash
uv run benchtrace diff <run-a> <run-b> tools-006
```

```text
A: correct (score 1.0)   B: incorrect (score 0.0)
Diverged at step 3 (model btmock/strong): differs: output.
│ 1   │ model btmock/strong │ calls calculator({'expression': '786 - 9855'}) │ calls calculator(...) │               │
│ 2   │ tool calculator     │ {'expression': '786 - 9855'} → -9069           │ ... → -9069           │               │
│ → 3 │ model btmock/strong │ The answer is -9069                            │ The answer is -9066   │ output        │
│ 4   │ scorer match        │ score 1.0 · answer -9069                       │ score 0.0 · answer ...│ answer, score │
```

The same views are in the web UI (section 8), which is easier for long traces.

## 5. Running real models without surprise bills

Install the benchmark suite and set a provider key. Model names use [Inspect's provider format](https://inspect.aisi.org.uk/models.html), such as `openai/gpt-4o-mini` or `anthropic/claude-haiku-4-5`.

```bash
uv sync --extra benchmarks
export OPENAI_API_KEY=...
```

benchtrace ships no real prices because they change. Add the models you use to `~/.benchtrace/pricing.yaml` (USD per million tokens):

```yaml
openai/gpt-4o-mini:
  input: 0.15
  output: 0.60
  as_of: 2026-10-01
  source: https://openai.com/api/pricing
```

Without a price you still get token counts, but you cannot set a budget cap. Check the price against your provider's page; the numbers above are an example.

The safe workflow is quote, approve, run:

```bash
# 1. Run 5 samples per model and estimate the full run, with a 95% range.
uv run benchtrace quote create gsm8k -m openai/gpt-4o-mini --limit 200

# 2. Approve it with a hard cap in USD across all models.
uv run benchtrace quote approve <quote-id> --cap 5

# 3. Run exactly what was quoted. The run stops when it reaches the cap.
uv run benchtrace run --quote <quote-id>
```

Commands that call paid models ask for confirmation, or take `--yes` in scripts. `--limit N` runs only the first N samples, which is the cheapest way to try a new benchmark. `--content metadata` keeps prompts and outputs out of the database if they are sensitive.

## 6. Tracing your own agent with the Python SDK

You can send traces from your own code, not just from benchmarks. Start the server, which also serves the web UI:

```bash
uv run benchtrace serve        # http://127.0.0.1:8321
```

The SDK has three building blocks: `bt.trace(...)` wraps one task, `bt.span(...)` wraps one step inside it, and `bt.record(...)` attaches content such as prompts and outputs.

```python
from anthropic import Anthropic
from benchtrace.sdk import Benchtrace

bt = Benchtrace("http://127.0.0.1:8321", content="full")
client = Anthropic()

def answer(ticket_id: str, question: str) -> str:
    with bt.trace("answer-ticket", task_id=ticket_id):
        with bt.span("call-model", kind="model", **{"gen_ai.request.model": "claude-haiku-4-5"}) as span:
            bt.record(span, "prompt", question)
            reply = client.messages.create(
                model="claude-haiku-4-5", max_tokens=512, messages=[{"role": "user", "content": question}]
            )
            span.set_attribute("gen_ai.usage.input_tokens", reply.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", reply.usage.output_tokens)
            text = reply.content[0].text
            bt.record(span, "output", text)
        return text

answer("t-1", "How do I reset my password?")
health = bt.flush()          # wait for delivery
print(health.receipts)       # {trace_id: {"state": "closed", "expected_spans": 2, "received_spans": 2}}
```

Tips:

1. **Span kinds.** Use `kind="model"` for LLM calls and `kind="tool"` for tool calls; the root from `bt.trace` is `agent`. `benchtrace diff` lines up steps by kind (and by name for non-model steps), so consistent kinds and names make diffs readable.
2. **Attribute names.** Use the OpenTelemetry GenAI names (`gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`) so token counts appear in traces.
3. **Content policy.** `content` is `"metadata"` (default: timings, tokens and structure only), `"redacted"` (content with common secrets masked) or `"full"`. The server's `BENCHTRACE_INGEST_CONTENT` (default `metadata`) is a ceiling: a client can ask for less, never more. To store prompts locally, start the server with `BENCHTRACE_INGEST_CONTENT=full`.
4. **Errors.** If code inside `bt.trace(...)` raises, the exception is recorded on the span, the trace is still sealed, and the exception propagates to your code as usual.
5. **Nothing is lost on a crash.** Spans are written to a local spool before sending and deleted only after the server confirms them. If your process dies or the server is down, the next `Benchtrace` client for the same URL sends them.
6. **Receipts.** A trace shows `closed` once every span it declared has arrived, or `incomplete` if some are missing.
7. **Existing instrumentation.** The SDK attaches to the global OpenTelemetry tracer provider, so spans from OpenInference or GenAI instrumentations (OpenAI, Anthropic, LangChain and others) that you enable in the same process are sent along. The next section shows this with two agent frameworks.

A complete offline version with a tool call and an error case is in [`examples/sdk_trace_agent.py`](../examples/sdk_trace_agent.py):

```bash
BENCHTRACE_INGEST_CONTENT=full uv run benchtrace serve      # terminal 1
uv run python examples/sdk_trace_agent.py                    # terminal 2
```

```text
q-1 → The answer is 391
q-2 → error: division by zero
trace 56df545106be88437b8e0cb5709c46af: closed (4/4 spans)
trace 8a3e011747956baddcc0697ee2ceaca7: closed (3/3 spans)
```

```bash
uv run benchtrace trace 8a3e011747956baddcc0697ee2ceaca7 --trace-id
```

```text
trace 8a3e011747956baddcc0697ee2ceaca7 · sdk
└── agent solve-question 4ms error
    ├── model call-model 0ms 25→12 tok
    └── tool calculator 2ms error
```

### Agent frameworks: OpenAI Agents SDK and LangChain

If your agent is built on a framework, you don't need to write spans by hand. Turn on the framework's [OpenInference](https://github.com/Arize-ai/openinference) instrumentation with the benchtrace provider, and wrap each task in `bt.trace(...)`. Every agent step, model call and tool call becomes a span, with model names, token counts and (if the content policy allows) prompts and outputs.

With the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/):

```python
from agents import Agent, Runner, function_tool
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from benchtrace.sdk import Benchtrace

bt = Benchtrace("http://127.0.0.1:8321", content="full")
OpenAIAgentsInstrumentor().instrument(tracer_provider=bt.provider)

@function_tool
def lookup_order(order_id: str) -> str:
    """Look up the status of an order by its id."""
    return ORDERS.get(order_id, "no order with that id")

agent = Agent(name="support-agent", instructions="...", model="gpt-4o-mini", tools=[lookup_order])
with bt.trace("support-ticket", task_id="t-1"):
    result = await Runner.run(agent, "Where is my order A-100?")
```

With [LangChain](https://docs.langchain.com/) agents, which run on LangGraph (the same instrumentation also traces graphs you build with LangGraph directly):

```python
from langchain.agents import create_agent
from openinference.instrumentation.langchain import LangChainInstrumentor
from benchtrace.sdk import Benchtrace

bt = Benchtrace("http://127.0.0.1:8321", content="full")
LangChainInstrumentor().instrument(tracer_provider=bt.provider)

agent = create_agent("openai:gpt-4o-mini", tools=[lookup_order], system_prompt="...")
with bt.trace("support-ticket", task_id="t-1"):
    result = agent.invoke({"messages": [{"role": "user", "content": "Where is my order A-100?"}]})
```

The full scripts are [`examples/openai_agents_trace.py`](../examples/openai_agents_trace.py) and [`examples/langchain_agent_trace.py`](../examples/langchain_agent_trace.py). They need an `OPENAI_API_KEY`. The frameworks aren't benchtrace dependencies, so `uv run --with` installs them just for the run:

```bash
uv run --with openai-agents --with openinference-instrumentation-openai-agents python examples/openai_agents_trace.py
uv run --with langchain --with langchain-openai --with openinference-instrumentation-langchain python examples/langchain_agent_trace.py
```

The span trees for one ticket look like this. Token counts here are from a test run against a stand-in API, so yours will differ:

```text
trace 73ea18977317f986efbf66a6f5bab19e · sdk          (OpenAI Agents SDK)
└── agent support-ticket 752ms
    └── agent Agent workflow 750ms
        └── span Agent workflow 750ms
            └── agent support-agent 749ms
                ├── span turn 735ms
                │   ├── model response 665ms 50→12 tok
                │   └── tool lookup_order 2ms
                └── span turn 13ms
                    └── model response 9ms 50→12 tok

trace 0c9853dc9ff4e98d4a6dfdc22ac9a5b9 · sdk          (LangChain)
└── agent support-ticket 46ms
    └── span LangGraph 41ms
        ├── span model 28ms
        │   └── model ChatOpenAI 24ms 50→12 tok
        ├── span tools 3ms
        │   └── tool lookup_order 1ms
        └── span model 6ms
            └── model ChatOpenAI 5ms 50→12 tok
```

Two details are worth knowing. Passing `tracer_provider=bt.provider` makes it explicit which provider the instrumentation reports to, which matters when your app already configures OpenTelemetry itself. And the Agents SDK instrumentation by default replaces the SDK's own export to the OpenAI traces dashboard; pass `exclusive_processor=False` to `instrument()` to keep both.

## 7. Sending traces from other languages

benchtrace accepts standard OTLP/HTTP at `/v1/traces`, in protobuf or JSON. Point any OpenTelemetry exporter at it:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:8321/v1/traces
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer bt_..."   # only when authentication is on
```

Span kinds come from `openinference.span.kind` or `gen_ai.operation.name` (`chat` is a model call, `execute_tool` a tool call, `invoke_agent` an agent). Unknown spans are kept as generic steps.

[`examples/otlp_json.sh`](../examples/otlp_json.sh) sends a two-span trace with `curl`, then seals it:

```text
{"partialSuccess":{},"benchtrace":{"batch_id":null,"accepted_spans":2,"duplicate":false}}
{"trace_id":"d2e0c5f770ccfd7381b7f85a3e8d952e","expected_spans":2,"received_spans":2,"state":"closed"}
```

Sealing (`POST /api/traces/<trace-id>/seal` with `{"expected_spans": N}`) is optional. Without it a trace stays `open`, because benchtrace cannot know whether more spans are coming.

## 8. Using the HTTP API (CI regression gate)

`benchtrace serve` runs the API, the web UI and two background workers. Build the UI once first:

```bash
npm --prefix web ci && npm --prefix web run build
uv run benchtrace serve
```

Useful endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/runs` | Start runs: `{"benchmark": "toy-arith", "models": ["btmock/strong"], "limit": 20}` |
| `GET /api/runs/<id>` | Status (`queued`, `running`, `succeeded`, `failed`, `cancelled`, `budget_exceeded`) and progress |
| `GET /api/compare?a=<id>&b=<id>` | The same comparison as `benchtrace compare --json` |
| `POST /api/quotes`, `POST /api/quotes/<id>/approve` | Quote and approve paid runs |
| `GET /api/traces?source=sdk` | Traces sent by the SDK |

Over the API, paid models need an approved quote unless the server sets `BENCHTRACE_REQUIRE_QUOTE=0`.

[`examples/api_regression_gate.py`](../examples/api_regression_gate.py) starts a baseline and a candidate run, waits, compares, and exits non-zero only on a clear regression (the whole 95% CI below the allowed drop). It uses only the Python standard library, so it runs in any CI image:

```bash
uv run python examples/api_regression_gate.py --limit 20
```

```text
Started run_a85b3c26ff37 (btmock/strong) and run_180064789b1d (btmock/weak)
  btmock/strong: running 12   btmock/weak: running 1
  btmock/strong: succeeded 20   btmock/weak: succeeded 20
Score 100.0% → 70.0% (Δ -30.0 pts, 95% CI -50.0 to -10.0, McNemar p=0.0312)
Regressed samples: arith-001, arith-007, arith-009, arith-011, arith-013, arith-017
Inspect one: benchtrace diff run_a85b3c26ff37 run_180064789b1d <sample>
```

The exit code was 1. With `--candidate btmock/strong` it reports no regressions and exits 0. For real models: `--benchmark gsm8k@1 --baseline openai/gpt-4o-mini --candidate anthropic/claude-haiku-4-5 --limit 50 --max-drop 0.02`.

## 9. Turning failures into a dataset

Wrong answers from a run, or traces imported from Langfuse, LangSmith or Braintrust, can become reviewed test cases:

```bash
uv run benchtrace dataset create "calculator failures"                        # prints ds_...
uv run benchtrace dataset add <dataset-id> --run <run-b> --outcome incorrect --split dev
uv run benchtrace dataset show <dataset-id>
uv run benchtrace dataset review 1 --reference "-9069" --split test --approve   # 1 is from the Item column
uv run benchtrace dataset export <dataset-id> -o tasks.jsonl                  # approved items only
```

Items start as drafts. The recorded output is kept as evidence but is never used as the reference answer: a reviewer sets the reference, a split and the usage rights before an item can be approved.

## 10. Troubleshooting

**`Blocked: Run B ... was served by more than one model revision`.** The provider changed the model behind the same name during the run (try `btmock/drifty` to see this). The comparison would be unsound, so rerun, or pin a dated model name. `--force` shows an exploratory comparison anyway.

**Comparison refused because variants differ.** The two runs used different benchmark variants, such as `gsm8k@1` and `gsm8k@2`. Compare runs of the same variant.

**`calls paid models ... Pass --yes to approve`.** Non-interactive shells cannot confirm, so pass `--yes` once you have checked the quote.

**`Paid models ... need an approved quote` from the API.** Create and approve a quote first, then start the run with `{"quote_id": "..."}`.

**A model has tokens but no cost, or rejects `--budget`.** Add its price to `~/.benchtrace/pricing.yaml` (section 5).

**SDK traces show no prompts or outputs.** The server stores `metadata` only by default. Start it with `BENCHTRACE_INGEST_CONTENT=full` (or `redacted`) and create the client with `content="full"`.

**SDK traces stay `open`.** The trace was not sealed yet (the process exited before `flush()`, or the server was down). Spooled batches are sent the next time a client for the same URL starts. `bt.health()` reports `pending_batches` and `last_error`.

**401 Unauthorized.** Authentication is on. Create a key with `benchtrace admin create-key --workspace <name> --name <purpose>` and set `BENCHTRACE_API_KEY` (the SDK and the example scripts read it).

**Where is my data?** `~/.benchtrace`, or `BENCHTRACE_HOME` if set. Original Inspect logs are under `logs/` and open in [Inspect View](https://inspect.aisi.org.uk/log-viewer.html).
