# Examples

Each example runs offline with the bundled mock models. The [user guide](../docs/guide.md) explains what the output means.

| Example | Shows | Run it |
| --- | --- | --- |
| [`cli_tour.sh`](cli_tour.sh) | Catalog, quote and budget cap, run, compare, trace, diff, dataset drafts | `bash examples/cli_tour.sh` |
| [`sdk_trace_agent.py`](sdk_trace_agent.py) | Tracing your own agent: model and tool spans, token counts, content, an error case, receipts | `uv run python examples/sdk_trace_agent.py` |
| [`otlp_json.sh`](otlp_json.sh) | Sending a trace as OTLP/JSON with `curl`, for apps not written in Python | `bash examples/otlp_json.sh` |
| [`api_regression_gate.py`](api_regression_gate.py) | Starting runs over the HTTP API and failing CI on a clear regression | `uv run python examples/api_regression_gate.py --limit 20` |

`cli_tour.sh` uses a throwaway `BENCHTRACE_HOME`. The other three talk to a running server:

```bash
BENCHTRACE_INGEST_CONTENT=full uv run benchtrace serve    # keep this running in another terminal
```

Set `BENCHTRACE_URL` to use a server other than `http://127.0.0.1:8321`, and `BENCHTRACE_API_KEY` when authentication is on.
