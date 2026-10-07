"""Trace a LangChain agent (built on LangGraph) into benchtrace.

    export OPENAI_API_KEY=...                                        # or another provider, see MODEL
    BENCHTRACE_INGEST_CONTENT=full uv run benchtrace serve            # in another terminal
    uv run --with langchain --with langchain-openai --with openinference-instrumentation-langchain \
        python examples/langchain_agent_trace.py
    uv run benchtrace trace <trace-id> --trace-id

The OpenInference instrumentation hooks LangChain's callbacks, so every graph step, model
call and tool call becomes an OpenTelemetry span that the benchtrace SDK delivers. The same
setup traces hand-built LangGraph graphs.
"""

from __future__ import annotations

import os

from langchain.agents import create_agent
from openinference.instrumentation.langchain import LangChainInstrumentor

from benchtrace.sdk import Benchtrace

# Any LangChain chat model string works, e.g. "anthropic:claude-haiku-4-5" with langchain-anthropic installed.
MODEL = os.environ.get("LANGCHAIN_MODEL", "openai:gpt-4o-mini")
ORDERS = {"A-100": "shipped on 3 Oct, arriving 8 Oct", "B-200": "being packed"}
TICKETS = {"t-1": "Where is my order A-100?", "t-2": "Has order B-200 shipped yet?"}


def lookup_order(order_id: str) -> str:
    """Look up the status of an order by its id, such as A-100."""
    return ORDERS.get(order_id, "no order with that id")


def main() -> None:
    bt = Benchtrace(os.environ.get("BENCHTRACE_URL", "http://127.0.0.1:8321"), content="full")
    LangChainInstrumentor().instrument(tracer_provider=bt.provider)

    agent = create_agent(
        MODEL,
        tools=[lookup_order],
        system_prompt="You answer order questions. Always look the order up before answering. Be brief.",
    )
    for ticket_id, question in TICKETS.items():
        # One benchtrace trace per ticket; the agent's spans nest under it.
        with bt.trace("support-ticket", task_id=ticket_id):
            result = agent.invoke({"messages": [{"role": "user", "content": question}]})
        print(f"{ticket_id} → {result['messages'][-1].content}")

    health = bt.flush()
    for trace_id, receipt in health.receipts.items():
        print(f"trace {trace_id}: {receipt['state']} ({receipt['received_spans']}/{receipt['expected_spans']} spans)")


if __name__ == "__main__":
    main()
