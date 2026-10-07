"""Trace an OpenAI Agents SDK agent into benchtrace.

    export OPENAI_API_KEY=...
    BENCHTRACE_INGEST_CONTENT=full uv run benchtrace serve        # in another terminal
    uv run --with openai-agents --with openinference-instrumentation-openai-agents \
        python examples/openai_agents_trace.py
    uv run benchtrace trace <trace-id> --trace-id

The OpenInference instrumentation turns the Agents SDK's own tracing (agent runs, model
calls, tool calls, handoffs) into OpenTelemetry spans, and the benchtrace SDK delivers them.
By default the instrumentation replaces the Agents SDK's export to the OpenAI dashboard;
pass exclusive_processor=False to `instrument()` to keep both.
"""

from __future__ import annotations

import asyncio
import os

from agents import Agent, Runner, function_tool
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

from benchtrace.sdk import Benchtrace

ORDERS = {"A-100": "shipped on 3 Oct, arriving 8 Oct", "B-200": "being packed"}
TICKETS = {"t-1": "Where is my order A-100?", "t-2": "Has order B-200 shipped yet?"}


@function_tool
def lookup_order(order_id: str) -> str:
    """Look up the status of an order by its id, such as A-100."""
    return ORDERS.get(order_id, "no order with that id")


agent = Agent(
    name="support-agent",
    instructions="You answer order questions. Always look the order up before answering. Be brief.",
    model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    tools=[lookup_order],
)


async def main() -> None:
    bt = Benchtrace(os.environ.get("BENCHTRACE_URL", "http://127.0.0.1:8321"), content="full")
    OpenAIAgentsInstrumentor().instrument(tracer_provider=bt.provider)

    for ticket_id, question in TICKETS.items():
        # One benchtrace trace per ticket; the agent's spans nest under it.
        with bt.trace("support-ticket", task_id=ticket_id):
            result = await Runner.run(agent, question)
        print(f"{ticket_id} → {result.final_output}")

    health = bt.flush()
    for trace_id, receipt in health.receipts.items():
        print(f"trace {trace_id}: {receipt['state']} ({receipt['received_spans']}/{receipt['expected_spans']} spans)")


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY first: this example calls the OpenAI API.")
    asyncio.run(main())
