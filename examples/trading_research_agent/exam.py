"""The exam: 30 research questions with known answers, worked out from the market data.

everyeval runs this as a benchmark (see catalog.yaml). The model name picks the agent version,
e.g. `-m mockllm/trader-v1`; no model is called, so it is free and offline.
"""

from __future__ import annotations

import functools
from itertools import combinations

from agent import AGENT_VERSIONS, TOOLS, research
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant, execute_tools, get_model
from inspect_ai.scorer import pattern
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.tool import Tool, ToolCall, ToolDef
from market_data import TICKERS, closes


def _change(ticker: str, days: int) -> float:
    prices = closes(ticker)
    return prices[-1] / prices[-1 - days] - 1


def questions() -> list[Sample]:
    samples = []
    for t in TICKERS:
        answer = "UP" if _change(t, 20) > 0 else "DOWN"
        samples.append(
            Sample(id=f"trend-{t}", input=f"Is {t} trending UP or DOWN over the last 20 trading days?", target=answer)
        )
    for a, b in list(combinations(TICKERS, 2))[::2][:14]:
        answer = a if _change(a, 20) > _change(b, 20) else b
        samples.append(
            Sample(id=f"compare-{a}-{b}", input=f"Which had the stronger 20-day return: {a} or {b}?", target=answer)
        )
    for t in TICKERS:
        prices = closes(t)
        answer = "YES" if prices[-1] > sum(prices[-50:]) / 50 else "NO"
        samples.append(Sample(id=f"avg50-{t}", input=f"Is {t} trading above its 50-day moving average?", target=answer))
    return samples


def _as_tool(fn) -> Tool:
    @functools.wraps(fn)
    async def run(**arguments):
        return str(fn(**arguments))[-200:]

    params = {name: name for name in fn.__annotations__ if name != "return"}
    return ToolDef(run, name=fn.__name__, description=fn.__name__, parameters=params).as_tool()


@solver
def trading_agent():
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        version = get_model().name
        if version not in AGENT_VERSIONS:
            raise ValueError(f"Unknown agent version {version!r}. Choose from: {', '.join(AGENT_VERSIONS)}")
        steps: list = []
        answer = research(state.input_text, version, steps)
        # Replay the agent's tool calls through Inspect so each one shows up in the trace.
        calls = [ToolCall(id=str(i), function=s["tool"], arguments=s["arguments"]) for i, s in enumerate(steps)]
        await execute_tools([ChatMessageAssistant(content="", tool_calls=calls)], [_as_tool(f) for f in TOOLS.values()])
        state.output.completion = answer
        return state

    return solve


@task
def trading_exam() -> Task:
    return Task(dataset=questions(), solver=trading_agent(), scorer=pattern(r"Answer:\s*(\w+)"))
