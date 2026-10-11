"""Harbor agents for tau3-bench. Run them from the repository root with everyeval:

    everyeval run tau3-retail  -m openai/gpt-5-mini --agent examples.tau3_agents.harbor_agents:RetailSupportAgent
    everyeval run tau3-retail  -m openai/gpt-5-mini --agent examples.tau3_agents.harbor_agents:PlainRetailAgent
    everyeval run tau3-banking -m openai/gpt-5-mini --agent examples.tau3_agents.harbor_agents:BankingAgent
    everyeval run tau3-banking -m openai/gpt-5-mini --agent examples.tau3_agents.harbor_agents:PlainBankingAgent

RetailSupportAgent is built on the OpenAI Agents SDK (retail_agent.py) and BankingAgent on LangGraph
(banking_agent.py). Each has a plain twin on the same framework without our design, to compare
against. Each class copies these files into the task container, where the benchmark's MCP server is
reachable, installs the agent's pinned dependencies into its own virtualenv (the grader runs in
the same container and must keep its own packages), and runs one conversation.

After the run the class reports token counts back to Harbor (everyeval prices them from its own
price list), adds the simulated customer's model spend as `environment_cost_usd` (everyeval counts
it toward budgets), and records the tau2-bench commit the task image was built from as
`benchmark_revision`.
"""

from __future__ import annotations

import json
import os
import random
import shlex
from pathlib import Path
from typing import Any, ClassVar

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

HERE = Path(__file__).parent
SOURCES = ("runner.py", "guards.py", "retail_agent.py", "banking_agent.py")
TARGET_DIR = "/opt/everyeval-tau3"
VENV = f"{TARGET_DIR}/venv"
INSTRUCTION_TARGET = "/tmp/everyeval_tau3_instruction.md"
SUMMARY = "tau3-agent-summary.json"
RUNTIME_STATE = "tau3_runtime_state.json"  # written by the benchmark's runtime server
REVISION_FILE = "tau2-bench-revision.txt"
# Every agent needs the MCP client to reach the benchmark.
COMMON_REQUIREMENTS = ("mcp==1.25.0",)
PASSTHROUGH_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "TAU3_AGENT_REASONING_EFFORT",
)


class _Tau3Agent(BaseInstalledAgent):
    capabilities = AgentCapabilities(mcp_servers=True)  # the benchmark runs behind the task's MCP server
    agent: ClassVar[str] = ""
    requirements: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self,
        *args: Any,
        seed: int | None = 300,
        trial_index: int = 0,
        reasoning_effort: str | None = None,
        max_steps: int = 200,
        max_errors: int = 10,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._seed = seed
        self._trial_index = trial_index
        self._reasoning_effort = reasoning_effort
        self._max_steps = max_steps
        self._max_errors = max_errors

    @staticmethod
    def name() -> str:
        return "everyeval-tau3"

    def version(self) -> str | None:
        return "1.0"

    async def install(self, environment: BaseEnvironment) -> None:
        packages = " ".join(shlex.quote(p) for p in (*COMMON_REQUIREMENTS, *self.requirements))
        await self.exec_as_root(
            environment,
            command=f"python3 -m venv {VENV} && {VENV}/bin/pip install --no-cache-dir -q {packages}",
        )
        for source in SOURCES:
            await environment.upload_file(HERE / source, f"{TARGET_DIR}/{source}")
        # The task image clones tau2-bench at build time; record which commit it got.
        await self.exec_as_root(
            environment,
            command=f"git -C /opt/tau2-bench rev-parse HEAD > /logs/agent/{REVISION_FILE} 2>/dev/null || true",
        )

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        if not self.model_name:
            raise ValueError("Pass a model with -m, e.g. openai/gpt-5-mini")
        servers = [s for s in self.mcp_servers if s.transport == "streamable-http" and s.url]
        if len(servers) != 1:
            raise ValueError("tau3 agents expect the task's single streamable-http MCP server (tau3-runtime)")
        local = self.logs_dir / "instruction.md"
        local.write_text(instruction, encoding="utf-8")
        await environment.upload_file(local, INSTRUCTION_TARGET)
        parts = [
            f"{VENV}/bin/python",
            f"{TARGET_DIR}/runner.py",
            "--agent",
            self.agent,
            "--model",
            self.model_name,
            "--instruction-file",
            INSTRUCTION_TARGET,
            "--mcp-url",
            servers[0].url,
            "--max-steps",
            str(self._max_steps),
            "--max-errors",
            str(self._max_errors),
            "--seed",
            "none" if self._seed is None else str(self._trial_seed()),
        ]
        if self._reasoning_effort:
            parts += ["--reasoning-effort", self._reasoning_effort]
        command = " ".join(shlex.quote(p) for p in parts) + " 2>&1 | tee /logs/agent/tau3-agent.txt"
        env = {k: v for k in PASSTHROUGH_ENV if (v := self._get_env(k))}
        env |= {k: v for k, v in os.environ.items() if k.startswith("LITELLM_") and v}
        await self.exec_as_agent(environment, command=f"set -o pipefail; {command}", env=env)

    def _trial_seed(self) -> int:
        """tau2-bench derives one seed per trial from the base seed; the parity agent does the same."""
        rng = random.Random(self._seed)
        seed = self._seed
        for _ in range(max(0, self._trial_index) + 1):
            seed = rng.randint(0, 1_000_000)
        return seed

    def populate_context_post_run(self, context: AgentContext) -> None:
        summary = _read_json(self.logs_dir / SUMMARY) or {}
        context.n_input_tokens = summary.get("n_input_tokens")
        context.n_output_tokens = summary.get("n_output_tokens")
        context.n_cache_tokens = summary.get("n_cache_tokens")
        metadata = dict(summary.get("metadata") or {})
        state = _read_json(self.logs_dir / RUNTIME_STATE) or {}
        user_costs = [m.get("cost") for m in state.get("messages") or [] if m.get("role") == "user"]
        if any(c is not None for c in user_costs):
            metadata["environment_cost_usd"] = sum(c or 0.0 for c in user_costs)
        revision = self.logs_dir / REVISION_FILE
        if revision.exists() and revision.read_text().strip():
            metadata["benchmark_revision"] = f"tau2-bench@{revision.read_text().strip()[:12]}"
        context.metadata = metadata


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class RetailSupportAgent(_Tau3Agent):
    """Retail support agent on the OpenAI Agents SDK, with authentication and confirmation guardrails."""

    agent = "retail"
    requirements = ("openai-agents==0.22.3", "openai==3.18.0")

    @staticmethod
    def name() -> str:
        return "everyeval-tau3-retail"


class BankingAgent(_Tau3Agent):
    """Bank support agent on LangGraph: research, model, guard and tool nodes, with case notes in state."""

    agent = "banking"
    requirements = ("langgraph==1.2.12", "langchain-openai==1.6.4", "langchain-core==1.6.4", "openai==3.18.0")

    @staticmethod
    def name() -> str:
        return "everyeval-tau3-banking"


class PlainRetailAgent(RetailSupportAgent):
    """The retail agent's plain twin on the same Agents SDK: generic prompt, no guardrails."""

    agent = "retail-plain"

    @staticmethod
    def name() -> str:
        return "everyeval-tau3-retail-plain"


class PlainBankingAgent(BankingAgent):
    """The banking agent's plain twin on LangGraph: a model -> tools loop, no research, guard or notes."""

    agent = "banking-plain"

    @staticmethod
    def name() -> str:
        return "everyeval-tau3-banking-plain"
