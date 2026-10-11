# Customer-service agents on tau3-bench

Two agents built the way teams build them today, on the two most common agent frameworks, each scored on a 2026 benchmark. Each is compared task by task with its *plain twin*: an agent on the same framework, model, tools and policy, without our design. The difference is what the design adds.

| Agent | Framework | Benchmark | Use case |
| --- | --- | --- | --- |
| `RetailSupportAgent` | [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) | `tau3-retail` (114 tasks) | Online store support: authenticate the customer, then cancel, modify, return or exchange orders under a written policy |
| `BankingAgent` | [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | `tau3-banking` (97 tasks) | Bank support over a 698-document knowledge base: find the procedure, verify identity, unlock the internal tools the documents name |
| `PlainRetailAgent` | OpenAI Agents SDK | `tau3-retail` | The retail agent's plain twin: a generic customer-service prompt with the policy, no guardrails |
| `PlainBankingAgent` | LangGraph | `tau3-banking` | The banking agent's plain twin: a model → tools loop with a generic prompt, no research, guard or case notes |

[tau3-bench](https://github.com/sierra-research/tau2-bench) is Sierra's 2026 release of the tau-bench family; `banking_knowledge` is new in it ([τ-Knowledge, 2026](https://arxiv.org/abs/2603.04370)). Each task runs a simulated customer and the domain's tools behind an MCP server. The verifier checks the final database state (and, for some tasks, what the agent told the customer) with tau2-bench's own evaluator. Reward is 0 or 1.

For a walkthrough aimed at teams shipping a support agent (quoting, comparing, reading diffs and traces, gating CI), with screenshots of these runs, see [Shipping a retail support agent](../../docs/retail-agent-guide.md).

## How the agents work

**RetailSupportAgent (OpenAI Agents SDK).** Each customer turn is one `Runner.run`. The benchmark's tools become `FunctionTool`s, and two policy rules are enforced as SDK tool input guardrails:

- *Authenticate first.* Account and order tools are rejected until `find_user_id_by_email` or `find_user_id_by_name_zip` has found the customer. The policy requires this even when the customer gives a user id.
- *Confirm before writing.* Cancel, modify, return and exchange calls are rejected unless the customer's latest message confirms. One confirmation allows one database change.

**BankingAgent (LangGraph).** Each customer turn runs a graph of four nodes:

- `research` searches the knowledge base with the customer's own words before the model answers, so every reply starts from the bank's documents.
- `agent` is the model, with the policy, a working procedure and the *case notes* pinned to the prompt: verified identity, unlocked tools and tools handed to the customer, kept in graph state so they survive long conversations.
- `guard` holds the first account change before `log_verification` (calling again goes through, since some flows need no verification), and sends back any unsearched "I can't help" reply or transfer.
- `tools` runs the call in the benchmark and updates the case notes.

The guard rules are in `guards.py`, shared and tested independently of either framework. A blocked step never reaches the benchmark; the model gets the reason and tries again. After 8 blocks in one conversation the guards stop blocking, so a confused model cannot loop forever.

## Run them

You need Docker, the Harbor extra, and an `OPENAI_API_KEY`: the simulated customer and the grader call OpenAI models.

```bash
uv sync --extra harbor
export OPENAI_API_KEY=...
```

Check the pipeline for free first. `oracle` replays each task's reference solution and should score 100%:

```bash
uv run benchtrace run tau3-retail -m oracle --limit 2
```

Then run an agent and its plain twin on the same tasks, from the repository root (the agent's import path is resolved from the current directory), and compare:

```bash
uv run benchtrace quote create tau3-banking -m openai/gpt-5-mini --agent examples.tau3_agents.harbor_agents:BankingAgent --limit 20 --sample-size 2
uv run benchtrace quote approve <quote-id> --cap 3
uv run benchtrace run --quote <quote-id>

uv run benchtrace run tau3-banking -m openai/gpt-5-mini --agent examples.tau3_agents.harbor_agents:PlainBankingAgent --limit 20 --budget 3 --yes

uv run benchtrace compare <plain-run> <agent-run>
uv run benchtrace trace <agent-run> <task>
```

The agents use OpenAI's own clients (the Agents SDK's, and `langchain-openai`) and take `openai/...` models. They report token counts and benchtrace prices them from `~/.benchtrace/pricing.yaml`, so every agent is priced the same way and budgets need the model's price there. Agent and simulated-customer spend both count toward the cap; the grader's one call per task does not.

## What the traces show

Each task's trace has the conversation as spans: every model call with its token counts, every tool call with its arguments and result, and the customer's messages. The LangGraph agent's research searches appear as `research` steps with no model call. A tool result starting with `Blocked:` or `Held:` is a guard firing, and the step's ATIF record names the guard. The trial's root span carries the agent's summary as `agent.*` attributes: how the conversation ended (`agent.stop_reason`) and how often each guard fired (`agent.guard_blocks`).

## Comparability and reproducibility

- **Benchmark version.** The Harbor tasks clone tau2-bench's latest commit when their images build; recent commits broke the grader (it imports voice-only dependencies the image lacks, so every task scored 0). The catalog entries patch a local copy of the dataset to build on v1.0.1, the release with the published grading fix. Each trial also records the commit it ran against, and `benchtrace compare` blocks runs on different benchmark code.
- **Simulated customer.** Its model is part of the catalog variant (`gpt-5-mini`, low reasoning effort), so scores here are comparable with each other but not with the public leaderboard, which uses `gpt-5.2`.
- **Isolation.** Each agent installs its pinned packages into its own virtualenv in the task container. The grader runs in the same container, so the agent must not change the grader's packages.
- **Sample size.** With 20 tasks a run, the confidence interval on a score difference is wide. Treat one small comparison as a demo, and read the per-task changes and traces for why.

## Files

- `harbor_agents.py`: the Harbor agent classes. Each copies the files below into the task container, installs its dependencies and runs `runner.py`.
- `runner.py`: the container entry point. Talks to the benchmark (`Tau3Runtime`), records the ATIF trajectory (`Recorder`), and starts the chosen agent.
- `retail_agent.py`, `banking_agent.py`: the two agents, each with a switch for its plain twin.
- `guards.py`: the guard rules both agents use.
- `requirements.txt`: the pinned packages the agents install, for running their tests locally:

```bash
uv run --isolated --no-project --with-editable . --with pytest \
  --with-requirements examples/tau3_agents/requirements.txt pytest tests/test_tau3_agents.py
```
