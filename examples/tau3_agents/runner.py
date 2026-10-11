"""Entry point for the tau3 agents inside a Harbor task container.

The task container reaches the benchmark's `tau3-runtime` MCP server, which holds the simulated
customer and the domain tools. This module connects to it, runs one conversation with the chosen
agent, and writes three files under /logs/agent:

    trajectory.json                 ATIF trajectory (everyeval turns it into spans)
    tau3-agent-summary.json         token counts, read back by the Harbor agent class
    tau3-agent-transcript.json      the raw conversation, for debugging

Agents (--agent):

    retail          retail_agent.py on the OpenAI Agents SDK, with guardrails
    retail-plain    the same Agents SDK agent without our design: a generic prompt, no guardrails
    banking         banking_agent.py on LangGraph: research, model, guard and tool nodes
    banking-plain   the same LangGraph agent reduced to a model -> tools loop with a generic prompt

All of them talk to the benchmark through `Tau3Runtime` and record steps with `Recorder`, so
their traces look the same in everyeval. Comparing an agent with its plain twin shows what the
design adds on the same framework and model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

STOP_TOKENS = ("###STOP###", "###TRANSFER###", "###OUT-OF-SCOPE###")
FIRST_AGENT_MESSAGE = "Hi! How can I help you today?"
ORCHESTRATION_TOOLS = {
    "configure_run",
    "get_runtime_status",
    "get_assistant_tool_schemas",
    "start_conversation",
    "submit_assistant_message",
    "submit_assistant_tool_calls",
    "send_message_to_user",
    "end_conversation",
    "record_termination",
}


# ---------------------------------------------------------------------------- the benchmark


class Tau3Runtime:
    """The tau3 runtime's MCP protocol: start, call tools, talk to the customer, end."""

    def __init__(self, session: Any, timeout_sec: float = 120.0) -> None:
        self.session = session
        self.timeout_sec = timeout_sec
        self.ended: str | None = None  # termination reason once the conversation is over

    async def tool_schemas(self, listed: list[Any]) -> list[dict[str, Any]]:
        """Domain tools as OpenAI function schemas: the runtime's own list, else MCP's minus orchestration."""
        payload = await self._json("get_assistant_tool_schemas", {}, default=False)
        if isinstance(payload, list):
            return payload
        out = []
        for tool in listed:
            name = _get(tool, "name")
            if name in ORCHESTRATION_TOOLS:
                continue
            params = deepcopy(_get(tool, "inputSchema") or {}) or {"type": "object", "properties": {}}
            params.setdefault("type", "object")
            params.setdefault("properties", {})
            fn = {"name": name, "description": str(_get(tool, "description") or name), "parameters": params}
            out.append({"type": "function", "function": fn})
        return out

    async def start(self, seed: int | None, max_steps: int, max_errors: int) -> str:
        """Start the conversation and return the customer's first message."""
        await self._json("configure_run", {"seed": seed, "max_steps": max_steps, "max_errors": max_errors}, default={})
        first = await self._text("start_conversation", {})
        status = await self._json("get_runtime_status", {}, default={})
        self.ended = status.get("termination_reason") or ("user_stop" if _stops(first) else None)
        return first

    async def call_tools(self, calls: list[dict[str, Any]], content: str | None = None) -> list[tuple[str, str]]:
        """Run tool calls ({id, name, arguments}) in the environment; returns (call id, result) pairs."""
        status = await self._json(
            "submit_assistant_tool_calls", {"tool_calls_json": json.dumps(calls), "content": content}
        )
        self.ended = status.get("termination_reason") or None
        return [(str(r.get("id", "")), str(r.get("content") or "")) for r in status.get("tool_results") or []]

    async def say(self, message: str) -> str | None:
        """Send a message to the customer; returns their reply (None once the conversation is over)."""
        status = await self._json("submit_assistant_message", {"message": message})
        self.ended = status.get("termination_reason") or None
        observation = status.get("observation")
        return observation if isinstance(observation, str) and observation else None

    async def end(self, reason: str) -> None:
        await self._json("record_termination", {"reason": reason}, default={})
        self.ended = reason

    async def _text(self, name: str, args: dict[str, Any]) -> str:
        result = await self.session.call_tool(name, args, read_timeout_seconds=timedelta(seconds=self.timeout_sec))
        parts = []
        for item in _get(result, "content") or []:
            text = _get(item, "text")
            parts.append(str(text) if text is not None else json.dumps(item if isinstance(item, dict) else str(item)))
        if not parts and _get(result, "structuredContent") is not None:
            parts.append(json.dumps(_get(result, "structuredContent")))
        text = "\n".join(parts)
        if _get(result, "isError"):
            # e.g. the simulated customer's model call failed; never mistake it for the conversation
            raise RuntimeError(f"tau3 runtime: {text}")
        return text

    async def _json(self, name: str, args: dict[str, Any], default: Any = None) -> Any:
        try:
            return json.loads(await self._text(name, args))
        except Exception:
            if default is not None:
                return default
            raise


def _stops(text: str) -> bool:
    return any(token in text for token in STOP_TOKENS)


# ---------------------------------------------------------------------------- the trajectory


@dataclass
class Recorder:
    """Builds the ATIF trajectory and usage totals as the conversation runs."""

    agent_name: str
    model: str
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    guard_blocks: dict[str, int] = field(default_factory=dict)
    _by_call: dict[str, dict[str, Any]] = field(default_factory=dict)

    def _add(self, step: dict[str, Any]) -> dict[str, Any]:
        step = {"step_id": len(self.steps) + 1, "timestamp": datetime.now(UTC).isoformat(), **step}
        self.steps.append(step)
        return step

    def system(self, text: str) -> None:
        self._add({"source": "system", "message": text})

    def customer(self, text: str) -> None:
        self._add({"source": "user", "message": text})

    def model_step(
        self,
        text: str | None,
        calls: list[dict[str, Any]],
        usage: dict[str, Any],
        served_by: str | None = None,
    ) -> dict[str, Any]:
        """One model call. `calls` are {id, name, arguments}; `usage` has prompt/completion/cached tokens."""
        usage = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "cached_tokens": int(usage.get("cached_tokens") or 0),
        }
        self.input_tokens += usage["prompt_tokens"]
        self.output_tokens += usage["completion_tokens"]
        self.cached_tokens += usage["cached_tokens"]
        step: dict[str, Any] = {
            "source": "agent",
            "model_name": served_by or self.model,
            "message": text or "",
            "metrics": usage,
        }
        if calls:
            step["tool_calls"] = [
                {"tool_call_id": c["id"], "function_name": c["name"], "arguments": c["arguments"]} for c in calls
            ]
            step["observation"] = {"results": []}
        step = self._add(step)
        for c in calls:
            self._by_call[c["id"]] = step
        return step

    def code_step(self, name: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
        """Tool calls the agent's code makes without a model call (ATIF llm_call_count 0)."""
        step = self._add(
            {
                "source": "agent",
                "message": "",
                "llm_call_count": 0,
                "tool_calls": [
                    {"tool_call_id": c["id"], "function_name": c["name"], "arguments": c["arguments"]} for c in calls
                ],
                "observation": {"results": []},
                "extra": {"code_step": name},
            }
        )
        for c in calls:
            self._by_call[c["id"]] = step
        return step

    def tool_result(self, call_id: str, text: str) -> None:
        step = self._by_call.get(call_id)
        if step is not None:
            step["observation"]["results"].append({"source_call_id": call_id, "content": text})

    def guard(self, step: dict[str, Any] | None, guard: str, reason: str, discarded: bool = False) -> None:
        """Mark a step whose tool call or reply a guard blocked."""
        self.guard_blocks[guard] = self.guard_blocks.get(guard, 0) + 1
        if step is not None:
            extra = step.setdefault("extra", {})
            extra.setdefault("guards", []).append({"name": guard, "reason": reason})
            if discarded:
                extra["discarded"] = True

    def step_for_call(self, call_id: str) -> dict[str, Any] | None:
        return self._by_call.get(call_id)

    def trajectory(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "ATIF-v1.8",
            "session_id": str(uuid.uuid4()),
            "agent": {
                "name": self.agent_name,
                "version": "1.0",
                "model_name": self.model,
                "tool_definitions": self.tool_schemas,
            },
            "steps": self.steps,
            "final_metrics": {
                "total_prompt_tokens": self.input_tokens,
                "total_completion_tokens": self.output_tokens,
                "total_cached_tokens": self.cached_tokens,
                "total_steps": len(self.steps),
            },
            "extra": extra,
        }


# ---------------------------------------------------------------------------- plain agents


# The generic customer-service prompt of tau2-bench's reference agent. The plain agents use it with
# the policy and nothing else, as a team would on day one with either framework.
PLAIN_INSTRUCTIONS = """<instructions>
You are a customer service agent that helps the user according to the <policy> provided below.
In each turn you can either:
- Send a message to the user.
- Make a tool call.
You cannot do both at the same time.

Try to be helpful and always follow the policy.
</instructions>
<policy>
{policy}
</policy>"""


def parse_arguments(text: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {"_unparsed": text}
    return parsed if isinstance(parsed, dict) else {"_value": parsed}


def _get(item: Any, key: str) -> Any:
    if item is None:
        return None
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def extract_policy(instruction: str) -> str:
    match = re.search(r"</instructions>\s*<policy>\s*(.*?)\s*</policy>\s*$", instruction, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    found = re.findall(r"<policy>\s*(.*?)\s*</policy>", instruction, flags=re.DOTALL)
    return found[-1].strip() if found else instruction.strip()


# ---------------------------------------------------------------------------- entry point


async def converse(args: argparse.Namespace, runtime: Tau3Runtime, recorder: Recorder, policy: str) -> Any:
    """Start the conversation and hand it to the chosen agent."""
    recorder.tool_schemas = await runtime.tool_schemas(_get(await runtime.session.list_tools(), "tools") or [])
    first = await runtime.start(args.seed, args.max_steps, args.max_errors)
    recorder.system(f"Scripted first agent message: {FIRST_AGENT_MESSAGE}")
    recorder.customer(first)
    if runtime.ended:
        return []
    common = {"model": args.model, "policy": policy, "tools": recorder.tool_schemas, "first_message": first}
    engineered = not args.agent.endswith("-plain")
    if args.agent.startswith("retail"):
        from retail_agent import run_retail_agent

        return await run_retail_agent(
            runtime, recorder, reasoning_effort=args.reasoning_effort, engineered=engineered, **common
        )
    from banking_agent import run_banking_agent

    return await run_banking_agent(
        runtime, recorder, reasoning_effort=args.reasoning_effort, engineered=engineered, **common
    )


async def main_async(args: argparse.Namespace) -> int:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    recorder = Recorder(agent_name=f"everyeval-tau3-{args.agent}", model=args.model)
    policy = extract_policy(Path(args.instruction_file).read_text(encoding="utf-8"))
    runtime: Tau3Runtime | None = None
    transcript: Any = None
    error: str | None = None
    timeout = args.read_timeout_sec
    try:
        async with streamablehttp_client(args.mcp_url, timeout=timeout, sse_read_timeout=timeout) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout)) as session:
                await session.initialize()
                runtime = Tau3Runtime(session, args.tool_timeout_sec)
                transcript = await converse(args, runtime, recorder, policy)
    except Exception as ex:  # noqa: BLE001 - write what we have, then fail the trial
        while isinstance(ex, BaseExceptionGroup) and len(ex.exceptions) == 1:
            ex = ex.exceptions[0]  # the MCP client wraps errors in task groups
        error = f"{type(ex).__name__}: {ex}"
    extra = {
        "agent": args.agent,
        "stop_reason": (runtime.ended if runtime else None) or ("error" if error else "unfinished"),
        "guard_blocks": recorder.guard_blocks,
        "error": error,
    }
    out = Path(args.logs_dir)
    _write(out / "trajectory.json", recorder.trajectory(extra))
    summary = {
        "n_input_tokens": recorder.input_tokens,
        "n_output_tokens": recorder.output_tokens,
        "n_cache_tokens": recorder.cached_tokens,
        "metadata": extra,  # no cost: everyeval prices tokens from its own price list
    }
    _write(out / "tau3-agent-summary.json", summary)
    _write(out / "tau3-agent-transcript.json", {"policy": policy, "conversation": transcript})
    if error:
        print(error)
        return 1
    return 0


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent", choices=["retail", "retail-plain", "banking", "banking-plain"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--instruction-file", required=True)
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--logs-dir", default="/logs/agent")
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--max-errors", type=int, default=10)
    parser.add_argument("--seed", type=lambda v: None if v == "none" else int(v), default=None)
    parser.add_argument("--reasoning-effort", default=os.environ.get("TAU3_AGENT_REASONING_EFFORT") or None)
    parser.add_argument("--read-timeout-sec", type=float, default=30.0)
    parser.add_argument("--tool-timeout-sec", type=float, default=120.0)
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
