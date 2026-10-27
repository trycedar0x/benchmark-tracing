# Examples

Evaluate existing agents and capture actual traces. Read [the SDK/CLI guide](../docs/evaluating-your-agent.md) and [coding-agent guide](../docs/agents.md).

| Example | Contract |
| --- | --- |
| [evaluate_callable.py](evaluate_callable.py) | Offline custom benchmark/callable fixture; `--yes` required; no server/provider |
| [sdk_trace_agent.py](sdk_trace_agent.py) | Deterministic SDK delivery check, not model quality |
| [otlp_json.sh](otlp_json.sh) | OTLP JSON delivery with curl |
| [openai_agents_trace.py](openai_agents_trace.py) | Real framework tracing; provider calls need approval |
| [langchain_agent_trace.py](langchain_agent_trace.py) | Real framework tracing; provider calls need approval |

Use a throwaway `EVERYBENCH_HOME`, unset current/legacy database URLs and run the callable fixture first. Tracing examples require a loopback `everybench serve` receiver; it defaults to metadata. Set `EVERYBENCH_INGEST_CONTENT` deliberately before capturing content. Shared/reverse-proxied servers require authentication and TLS.

Portable helpers remain self-contained in their skill folders: [SDK smoke](../skills/everybench-sdk/scripts/trace_smoke.py) and [read-only comparison gate](../skills/everybench/scripts/evaluate.py). The latter takes two existing run IDs and does not create jobs. Old model/Harbor/quote execution examples have been removed.
