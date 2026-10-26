# Examples

Examples for tracing agents and evaluating real models. Benchmark and framework examples require provider credentials; the SDK and OTLP tracing examples do not. The [user guide](../docs/guide.md) explains setup and output. The API regression script starts runs directly and requires a server explicitly configured with `EVERYBENCH_REQUIRE_QUOTE=0`.

| Example | Shows | Run it |
| --- | --- | --- |
| [`sdk_trace_agent.py`](sdk_trace_agent.py) | SDK tracing without a model API: model and tool spans, content, errors and receipts | `uv run python examples/sdk_trace_agent.py` |
| [`openai_agents_trace.py`](openai_agents_trace.py) | OpenAI Agents SDK tracing through OpenInference; needs `OPENAI_API_KEY` | `uv run --with openai-agents --with openinference-instrumentation-openai-agents python examples/openai_agents_trace.py` |
| [`langchain_agent_trace.py`](langchain_agent_trace.py) | LangChain/LangGraph tracing; needs `OPENAI_API_KEY` | `uv run --with langchain --with langchain-openai --with openinference-instrumentation-langchain python examples/langchain_agent_trace.py` |
| [`tau3_agents/`](tau3_agents) | Retail and bank support agents scored on tau3-bench; needs Docker and provider credentials | See its [README](tau3_agents/README.md) |
| [`otlp_json.sh`](otlp_json.sh) | Sending OTLP/JSON traces with `curl` | `bash examples/otlp_json.sh` |
| [`api_regression_gate.py`](api_regression_gate.py) | Failing CI on a clear regression; paid model calls | `uv run python examples/api_regression_gate.py --benchmark gsm8k --baseline openai/gpt-4o-mini --candidate anthropic/claude-haiku-4-5 --limit 20` |

The SDK, OTLP and HTTP API examples talk to a running server:

```bash
EVERYBENCH_INGEST_CONTENT=full uv run everybench serve    # keep this running in another terminal
```

Set `EVERYBENCH_URL` to use a server other than `http://127.0.0.1:8321`, and `EVERYBENCH_API_KEY` when authentication is on.

## Development smoke test

`bash examples/cli_tour.sh` exercises internal fixtures with mock models in a throwaway `EVERYBENCH_HOME`. It is a developer test, not a supported product benchmark or a model-quality evaluation.
