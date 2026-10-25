# everybench skills

Portable [Agent Skills](https://agentskills.io/) directories for coding agents using everybench in **their own projects**. These are not repository contributor instructions; those live in [AGENTS.md](../AGENTS.md).

| Skill | Use when | Interface |
| --- | --- | --- |
| [everybench](everybench/SKILL.md) | Choose a benchmark, run baseline/candidate evaluations, interpret paired results, or gate a change in CI | HTTP API and CLI |
| [everybench-sdk](everybench-sdk/SKILL.md) | Instrument an agent, integrate framework tracing, verify delivery, or diagnose a trace | Python SDK and OTLP; HTTP API for inspection |

The SDK skill is independent: tracing your own application does not require running a benchmark. The benchmark skill does not pretend the tracing SDK has evaluation methods.

## Install from this checkout

Copy only the skills you need into a skill directory supported by your coding agent. For agents that discover project skills under `.agents/skills`, run from this repository root, replacing the destination with your consuming project:

```bash
mkdir -p /path/to/your-project/.agents/skills
cp -R skills/everybench skills/everybench-sdk /path/to/your-project/.agents/skills/
```

For Claude Code, use the consuming project's `.claude/skills/` instead. If your agent uses a different skill directory, copy the same complete folders there. Do not overwrite an existing customized skill without reviewing it.

Keep `SKILL.md`, `references/` and `scripts/` together. Resolve script paths relative to the installed skill directory, not the agent's working directory. Installing a skill does not install the Python package, launch the server, grant budget approval or provision credentials.

## Read without installing

Start with [the agent integration guide](../docs/agents.md), then load the relevant `SKILL.md`. Each skill contains its own safety rules and links to bundled details only when needed.

The regression helper in `everybench/scripts/evaluate.py` uses only the Python standard library. The SDK smoke example in `everybench-sdk/scripts/trace_smoke.py` requires `everybench`. Both work outside this clone against a running server and emit a single JSON report.
