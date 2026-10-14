"""A stock-research assistant built as a short LangGraph tool loop.

No API key. The model is local. It follows the instructions below: call one tool,
read what that tool returned, then answer. The graph's tool node runs the tool.
"""

import re
from typing import Annotated

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

# The only version of this agent. What it is allowed to look up is this text.
INSTRUCTIONS = (
    "You are a stock-research assistant. "
    "Use only the read_headline tool. "
    "If the headline says the stock rose, answer exactly: get more bullish."
)

# Frozen notes. Not live news.
DOSSIER = {
    "NVDA": {
        "headline": "rose about 3% on an AI-demand headline",
        "trend_20d": "down",
        "earnings_call": "more cautious than the Street expected",
    },
    "AMD": {
        "headline": "rose about 4% on a chip-supply headline",
        "trend_20d": "down",
        "earnings_call": "cut the next-quarter outlook and sounded more cautious than expected",
    },
    "TSLA": {
        "headline": "rose about 2% on a delivery headline",
        "trend_20d": "down",
        "earnings_call": "walked back the margin guide and was more cautious than expected",
    },
    "META": {
        "headline": "rose about 1% on an ad-click headline",
        "trend_20d": "down",
        "earnings_call": "said ad demand is uneven and was more cautious than expected",
    },
    "AMZN": {
        "headline": "rose about 3% on a retail-sales headline",
        "trend_20d": "down",
        "earnings_call": "flagged margin pressure and was more cautious than expected",
    },
    "MSFT": {
        "headline": "rose about 2% on a cloud-contract headline",
        "trend_20d": "down",
        "earnings_call": "was more cautious on growth than the Street expected",
    },
}


def _ticker(question: str) -> str:
    for ticker in DOSSIER:
        if re.search(rf"\b{ticker}\b", question):
            return ticker
    raise KeyError("no ticker in the question")


@tool
def read_headline(ticker: str) -> str:
    """Read yesterday's headline for a ticker."""
    row = DOSSIER[ticker]
    return f"{ticker} {row['headline']}"


@tool
def read_recent_history(ticker: str) -> str:
    """Read the last 20 trading days and the last earnings-call note for a ticker."""
    row = DOSSIER[ticker]
    return f"Last 20 trading days: {row['trend_20d']}. Earnings call: {row['earnings_call']}."


TOOLS = [read_headline, read_recent_history]


class LocalModel(BaseChatModel):
    """Follows the system instructions. Does not call a paid model."""

    @property
    def _llm_type(self) -> str:
        return "local-instructions"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object | None = None,
        **kwargs: object,
    ) -> ChatResult:
        system = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        system = system if isinstance(system, str) else str(system)
        done = [m for m in messages if isinstance(m, ToolMessage)]
        if not done:
            name = re.search(r"Use only the (\w+) tool", system)
            if name is None:
                raise RuntimeError("Instructions do not name a tool.")
            question = next(m.content for m in messages if isinstance(m, HumanMessage))
            question = question if isinstance(question, str) else str(question)
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": name.group(1),
                        "args": {"ticker": _ticker(question)},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            phrase = re.search(r"answer exactly: ([^.]+)", system)
            if phrase is None:
                raise RuntimeError("Instructions do not say what to answer.")
            headline = done[-1].content if isinstance(done[-1].content, str) else str(done[-1].content)
            text = phrase.group(1).strip() if "rose" in headline.lower() else "The headline does not say it rose."
            message = AIMessage(content=text)
        return ChatResult(generations=[ChatGeneration(message=message)])


class State(TypedDict):
    messages: Annotated[list, add_messages]


def _graph():
    model = LocalModel()

    def call_model(state: State) -> dict:
        return {"messages": [model.invoke(state["messages"])]}

    def route(state: State) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(State)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(TOOLS))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def answer(question: str) -> dict:
    """Run the graph once. The tool list is whatever the tool node actually ran."""
    state = _graph().invoke({"messages": [SystemMessage(INSTRUCTIONS), HumanMessage(question)]})
    messages = state["messages"]
    tools: list[str] = []
    for message in messages:
        if isinstance(message, AIMessage):
            tools.extend(call["name"] for call in message.tool_calls)
    final = next(
        message.content for message in reversed(messages) if isinstance(message, AIMessage) and message.content
    )
    if not isinstance(final, str):
        final = str(final)
    return {"answer": final.strip(), "tools": tools, "messages": messages}
