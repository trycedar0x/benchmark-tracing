# Examples

Examples for evaluating existing agents, tracing and legacy model-runner comparisons. The [SDK/CLI guide](../docs/evaluating-your-agent.md) describes public external-agent contracts; the [user guide](../docs/guide.md) covers setup and interpretation. The callable fixture, SDK delivery check and OTLP examples need no provider keys. Real benchmark/framework runs require explicit authorization and credentials. The older direct API regression example requires a server deliberately configured for direct paid creation; prefer approved quotes for automated legacy paid runs.

| Example | Shows | Run it |
| --- | --- | --- |
| [`evaluate_callable.py`](evaluate_callable.py) | Shared evaluation SDK with deterministic callable/custom benchmark; no provider or server, not a public benchmark | `uv run python examples/evaluate_callable.py --yes` |
| [`sdk_trace_agent.py`](sdk_trace_agent.py) | Standalone SDK model/tool traces, content, errors and delivery receipts; no model API | `uv run python examples/sdk_trace_agent.py` |
| [`openai_agents_trace.py`](openai_agents_trace.py) | OpenAI Agents tracing through OpenInference; requires authorized provider calls | `uv run --with openai-agents --with openinference-instrumentation-openai-agents python examples/openai_agents_trace.py` |
| [`langchain_agent_trace.py`](langchain_agent_trace.py) | LangChain/LangGraph tracing; requires authorized provider calls | `uv run --with langchain --with langchain-openai --with openinference-instrumentation-langchain python examples/langchain_agent_trace.py` |
| [`tau3_agents/`](tau3_agents) | Legacy Harbor support agents; requires Docker and provider permission | See its [README](tau3_agents/README.md) |
| [`otlp_json.sh`](otlp_json.sh) | OTLP/JSON delivery using curl | `bash examples/otlp_json.sh` |
| [`api_regression_gate.py`](api_regression_gate.py) | Legacy direct model-runner regression example, not external HTTP-agent invocation | See script help and explicit server/spend policy |
| [`finance_catalog.yaml`](finance_catalog.yaml) | Optional internal Inspect baseline, not the default user-agent path | See [finance notes](../docs/finance-evaluations.md) |

Only the tracing/OTLP/API examples need a running receiver/server; the generic callable evaluation fixture does not. For standalone evidence capture, explicitly choose the producer and receiver policy:

```bash
EVERYBENCH_INGEST_CONTENT=full uv run everybench serve    # keep this running in another terminal
```

Set `EVERYBENCH_URL` to use a server other than `http://127.0.0.1:8321`, and `EVERYBENCH_API_KEY` when authentication is on.

## Portable coding-agent examples

Read [the agent integration guide](../docs/agents.md). The skills bundle two existing server-based helpers usable outside this checkout:

| Example | Contract | Run from this repository |
| --- | --- | --- |
| [SDK delivery smoke test](../skills/everybench-sdk/scripts/trace_smoke.py) | Fake model/tool metadata trace; JSON health; exit 0 delivered, 2 incomplete | `uv run python skills/everybench-sdk/scripts/trace_smoke.py --timeout 10` |
| [Legacy baseline/candidate gate](../skills/everybench/scripts/evaluate.py) | Two mock model-runner jobs; finite polling and JSON comparison, not arbitrary external-agent invocation | `uv run python skills/everybench/scripts/evaluate.py --candidate mock/strong --timeout 120` |

Both default to offline fake/mock operations, require a server, and grant no permission to spend. The API helper uses the standard library; the SDK example requires EveryBench. The [portable SDK evaluation reference](../skills/everybench-sdk/references/evaluation.md) separately documents module-level `evaluate` / `evaluate_sync`.

## Development smoke test

`bash examples/cli_tour.sh` exercises internal fixtures with mock models in a throwaway `EVERYBENCH_HOME`. It is a developer test, not a supported product benchmark or a model-quality evaluation.
