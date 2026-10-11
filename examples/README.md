# Examples

Each example runs offline with the bundled mock models, except the two agent framework examples and the tau3-bench agents, which call a real model API. The [user guide](../docs/guide.md) explains what the output means.

| Example | Shows | Run it |
| --- | --- | --- |
| [`cli_tour.sh`](cli_tour.sh) | Catalog, quote and budget cap, run, compare, trace, diff, dataset drafts | `bash examples/cli_tour.sh` |
| [`sdk_trace_agent.py`](sdk_trace_agent.py) | Tracing your own agent: model and tool spans, token counts, content, an error case, receipts | `uv run python examples/sdk_trace_agent.py` |
| [`openai_agents_trace.py`](openai_agents_trace.py) | An [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) agent with a tool, traced through OpenInference. Needs `OPENAI_API_KEY` | `uv run --with openai-agents --with openinference-instrumentation-openai-agents python examples/openai_agents_trace.py` |
| [`langchain_agent_trace.py`](langchain_agent_trace.py) | The same agent built with LangChain's `create_agent` (runs on LangGraph). Needs `OPENAI_API_KEY`, or set `LANGCHAIN_MODEL` for another provider | `uv run --with langchain --with langchain-openai --with openinference-instrumentation-langchain python examples/langchain_agent_trace.py` |
| [`tau3_agents/`](tau3_agents) | Two agents for real use cases, scored on tau3-bench (2026): retail support on the OpenAI Agents SDK and bank support on LangGraph, each compared with a plain agent on the same framework. Needs Docker and `OPENAI_API_KEY` | `uv run everyeval run tau3-retail -m openai/gpt-5-mini --agent examples.tau3_agents.harbor_agents:RetailSupportAgent` (see its [README](tau3_agents/README.md)) |
| [`otlp_json.sh`](otlp_json.sh) | Sending a trace as OTLP/JSON with `curl`, for apps not written in Python | `bash examples/otlp_json.sh` |
| [`api_regression_gate.py`](api_regression_gate.py) | Starting runs over the HTTP API and failing CI on a clear regression | `uv run python examples/api_regression_gate.py --limit 20` |

`cli_tour.sh` uses a throwaway `EVERYEVAL_HOME`. The others talk to a running server:

```bash
EVERYEVAL_INGEST_CONTENT=full uv run everyeval serve    # keep this running in another terminal
```

Set `EVERYEVAL_URL` to use a server other than `http://127.0.0.1:8321`, and `EVERYEVAL_API_KEY` when authentication is on.
