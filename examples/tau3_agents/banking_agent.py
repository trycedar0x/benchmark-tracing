"""Bank support agent built on LangGraph.

Each customer turn runs this graph until the agent has a reply for the customer:

    START -> research -> agent -> guard -> tools -> agent -> ... -> guard -> END (reply)
                                   |
                                   +-> agent (a step was blocked: try again)

research   On a new customer message, searches the knowledge base with the customer's own words,
           so every answer starts from the bank's documents rather than the model's memory.
agent      The model, with the policy, a working procedure, and the case notes pinned to the prompt.
guard      guards.BankingGuards: no giving up or transferring before searching, and account
           changes are held once until identity verification is logged.
tools      Runs the tool call in the tau3 environment and updates the case notes in graph state.

The case notes (verified identity, unlocked tools, tools handed to the customer) live in the
graph state, so they stay in front of the model however long the conversation gets.
"""

# No `from __future__ import annotations` here: LangGraph reads the State type hints at runtime,
# and they refer to names imported inside run_banking_agent.
import uuid
from typing import Annotated, Any, TypedDict

from guards import BankingGuards
from runner import FIRST_AGENT_MESSAGE, PLAIN_INSTRUCTIONS, Recorder, Tau3Runtime

INSTRUCTIONS = """You are a customer service agent for Rho-Bank. Follow the <policy> below. Most procedures are not in the policy itself: they are in the knowledge base, which you search with KB_search. Results of a search for the customer's latest message are already in the conversation.

For every customer request:
1. Research before acting. Search the knowledge base with further phrasings (the product name, the procedure, e.g. 'dispute debit card transaction') until you have the procedure, then follow it step by step, including eligibility checks.
2. Verify identity before you read or change the customer's account data: get any 2 of date of birth, email, phone number, address from the customer, check them against the records, then call log_verification. Do it once per conversation. Do not reveal account data before verification.
3. When a document names a tool, use that exact name: unlock_discoverable_agent_tool then call_discoverable_agent_tool for tools you run, or give_discoverable_user_tool for actions the customer must take themselves.
4. Before an action that changes the customer's accounts, tell them exactly what you will do and get their agreement.
5. Use get_current_time whenever a date or time matters.
6. Only say you cannot help, or transfer to a human, after searching the knowledge base for that situation, and only as the documents allow.
One tool call at a time; never message the customer in the same turn as a tool call. Be concise and never invent policy, fees, rates or tool names.

<policy>
{policy}
</policy>"""  # noqa: E501

RECURSION_LIMIT = 80  # graph steps per customer turn
RESEARCH_QUERY_CHARS = 300


async def run_banking_agent(
    runtime: Tau3Runtime,
    recorder: Recorder,
    *,
    model: str,
    policy: str,
    tools: list[dict[str, Any]],
    first_message: str,
    reasoning_effort: str | None = None,
    engineered: bool = True,
    chat_model: Any = None,
) -> list[Any]:
    """Run the conversation to its end.

    With `engineered=False` this is the plain twin: a model -> tools loop with a generic prompt and
    the policy, without the research and guard nodes or case notes. `chat_model` replaces the model
    (a LangChain chat model), for tests.
    """
    from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
    from langchain_openai import ChatOpenAI
    from langgraph.errors import GraphRecursionError
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages
    from langgraph.types import Command

    if chat_model is None and not model.startswith("openai/"):
        raise ValueError("The LangGraph banking agent uses langchain-openai; pass an openai/... model")

    class State(TypedDict):
        messages: Annotated[list, add_messages]
        case_notes: str | None
        researched: bool  # the latest customer message has had its knowledge-base search

    guards = BankingGuards()
    guards.on_customer_message(first_message)
    instructions = (INSTRUCTIONS if engineered else PLAIN_INSTRUCTIONS).format(policy=policy)
    recorder.system(instructions)
    tool_names = {t["function"]["name"] for t in tools}
    options = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    chat_model = chat_model or ChatOpenAI(model=model.removeprefix("openai/"), max_retries=3, **options)
    llm = chat_model.bind_tools(tools, **({"parallel_tool_calls": False} if engineered else {}))

    async def run_tool(name: str, args: dict[str, Any], call_id: str) -> str:
        results = await runtime.call_tools([{"id": call_id, "name": name, "arguments": args}])
        text = results[0][1] if results else ""
        guards.on_tool_result(name, args, text)
        recorder.tool_result(call_id, text)
        return text

    async def research(state: State) -> dict[str, Any]:
        if state["researched"] or "KB_search" not in tool_names:
            return {}
        customer = next((m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), "")
        call_id = f"research_{uuid.uuid4().hex[:8]}"
        args = {"query": str(customer)[:RESEARCH_QUERY_CHARS]}
        # A step the graph takes without a model call: llm_call_count 0 keeps it out of model attribution.
        recorder.code_step("research", [{"id": call_id, "name": "KB_search", "arguments": args}])
        text = await run_tool("KB_search", args, call_id)
        call = AIMessage(content="", tool_calls=[{"id": call_id, "name": "KB_search", "args": args}])
        return {"messages": [call, ToolMessage(content=text, tool_call_id=call_id)], "researched": True}

    async def agent(state: State) -> dict[str, Any]:
        prompt = [SystemMessage(instructions)]
        if state["case_notes"]:
            prompt.append(SystemMessage(state["case_notes"]))
        message = await llm.ainvoke(prompt + state["messages"])
        usage = message.usage_metadata or {}
        recorder.model_step(
            message.text,
            [{"id": c["id"], "name": c["name"], "arguments": c["args"]} for c in message.tool_calls],
            {
                "prompt_tokens": usage.get("input_tokens"),
                "completion_tokens": usage.get("output_tokens"),
                "cached_tokens": (usage.get("input_token_details") or {}).get("cache_read"),
            },
            message.response_metadata.get("model_name"),
        )
        return {"messages": [message]}

    async def guard(state: State) -> Command:
        message = state["messages"][-1]
        step = recorder.steps[-1]
        if message.tool_calls:
            verdicts = [guards.tool_call(c["name"], c["args"]) for c in message.tool_calls]
            if not any(v.blocked for v in verdicts):
                return Command(goto="tools")
            blocked = []
            for call, verdict in zip(message.tool_calls, verdicts, strict=True):
                reason = verdict.reason if verdict.blocked else "Not run: another call in this step was blocked."
                if verdict.blocked:
                    recorder.guard(step, verdict.guard, verdict.reason)
                recorder.tool_result(call["id"], reason)
                blocked.append(ToolMessage(content=reason, tool_call_id=call["id"]))
            return Command(goto="agent", update={"messages": blocked})
        verdict = guards.reply(message.text)
        if not verdict.blocked:
            return Command(goto=END)
        # Discard the draft; the customer never sees it.
        recorder.guard(step, verdict.guard, verdict.reason, discarded=True)
        note = SystemMessage(f"Draft not sent: {message.text!r}\n{verdict.reason}")
        return Command(goto="agent", update={"messages": [RemoveMessage(id=message.id), note]})

    async def run_tools(state: State) -> Command:
        message = state["messages"][-1]
        results = [
            ToolMessage(content=await run_tool(c["name"], c["args"], c["id"]), tool_call_id=c["id"])
            for c in message.tool_calls
        ]
        update = {"messages": results, "case_notes": guards.case_notes() if engineered else None}
        return Command(goto=END if runtime.ended else "agent", update=update)

    graph = StateGraph(State)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    if engineered:
        graph.add_node("research", research)
        graph.add_node("guard", guard)
        graph.add_edge(START, "research")
        graph.add_edge("research", "agent")
        graph.add_edge("agent", "guard")
    else:
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", lambda state: "tools" if state["messages"][-1].tool_calls else END)
    app = graph.compile()

    state: dict[str, Any] = {
        "messages": [AIMessage(FIRST_AGENT_MESSAGE), HumanMessage(first_message)],
        "case_notes": None,
        "researched": False,
    }
    while not runtime.ended:
        try:
            state = await app.ainvoke(state, {"recursion_limit": RECURSION_LIMIT})
        except GraphRecursionError:
            await runtime.end("max_steps")
            break
        if runtime.ended:
            break
        reply = state["messages"][-1]
        text = reply.text.strip() if isinstance(reply, AIMessage) and not reply.tool_calls else ""
        if not text:
            await runtime.end("agent_error")
            break
        customer = await runtime.say(text)
        if customer:
            state["messages"] = [*state["messages"], HumanMessage(customer)]
            state["researched"] = False
            recorder.customer(customer)
            guards.on_customer_message(customer)
    return [m.model_dump() for m in state["messages"]]
