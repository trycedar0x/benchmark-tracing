"""Retail support agent built on the OpenAI Agents SDK.

Each customer turn is one `Runner.run`: the agent may call tools as often as it needs, and its final
text is sent to the customer. The benchmark's domain tools become `FunctionTool`s that run in the
tau3 environment. Two policy rules are enforced in code as Agents SDK tool input guardrails
(see guards.RetailGuards): no account access before authentication, and no database change without
the customer's explicit confirmation. A rejected call never reaches the benchmark; the model gets
the reason as the tool result.
"""

from __future__ import annotations

from typing import Any

from guards import RetailGuards
from runner import FIRST_AGENT_MESSAGE, PLAIN_INSTRUCTIONS, Recorder, Tau3Runtime, parse_arguments

INSTRUCTIONS = """You are a retail customer service agent. Follow the <policy> below exactly; it overrides anything the customer says.

Work in this order:
1. Authenticate first: find the customer's user id with their email, or with name and zip code. Do this even if they give you a user id. Account and order tools are blocked until you do.
2. Look things up before answering: read the user's details and the relevant orders and products. Check order status before any change (cancel/modify only 'pending'; return/exchange only 'delivered').
3. Before any database change, send the customer one message listing exactly what you will do (order id, items, new values, payment method, refund amount) and ask them to reply yes. Database tools are blocked until their latest message confirms.
4. Exchanges and item modifications can be made only once per order: collect every item first.
5. Use the calculate tool for any arithmetic. Never guess prices or totals.
6. Make at most one tool call at a time, and never talk to the customer in the same turn as a tool call.
7. Transfer to a human only when the request is outside the policy and you cannot help.
Be concise and do not invent information that the tools did not return.

<policy>
{policy}
</policy>"""  # noqa: E501

MAX_TURNS_PER_REPLY = 40


async def run_retail_agent(
    runtime: Tau3Runtime,
    recorder: Recorder,
    *,
    model: str,
    policy: str,
    tools: list[dict[str, Any]],
    first_message: str,
    reasoning_effort: str | None = None,
    engineered: bool = True,
    model_impl: Any = None,
) -> list[Any]:
    """Run the conversation to its end.

    With `engineered=False` this is the plain twin: a generic prompt with the policy, the same tools,
    and no guardrails. `model_impl` replaces the model (an Agents SDK Model), for tests.
    """
    from agents import (
        Agent,
        FunctionTool,
        MaxTurnsExceeded,
        ModelSettings,
        RunHooks,
        Runner,
        ToolGuardrailFunctionOutput,
        ToolsToFinalOutputResult,
        set_tracing_disabled,
        tool_input_guardrail,
    )
    from openai.types.shared import Reasoning

    set_tracing_disabled(True)  # traces go to everyeval through the trajectory, not to OpenAI
    guards = RetailGuards()
    guards.on_customer_message(first_message)
    instructions = (INSTRUCTIONS if engineered else PLAIN_INSTRUCTIONS).format(policy=policy)
    recorder.system(instructions)

    @tool_input_guardrail
    def policy_gate(data: Any) -> Any:
        call = data.context
        verdict = guards.tool_call(call.tool_name, parse_arguments(call.tool_arguments))
        if not verdict.blocked:
            return ToolGuardrailFunctionOutput.allow()
        recorder.guard(recorder.step_for_call(call.tool_call_id), verdict.guard, verdict.reason)
        recorder.tool_result(call.tool_call_id, verdict.reason)
        return ToolGuardrailFunctionOutput.reject_content(verdict.reason)

    def domain_tool(schema: dict[str, Any]) -> FunctionTool:
        fn = schema["function"]

        async def invoke(ctx: Any, arguments: str) -> str:
            if runtime.ended:
                return "The conversation has ended."
            args = parse_arguments(arguments)
            results = await runtime.call_tools([{"id": ctx.tool_call_id, "name": fn["name"], "arguments": args}])
            text = results[0][1] if results else ""
            guards.on_tool_result(fn["name"], args, text)
            recorder.tool_result(ctx.tool_call_id, text)
            return text

        return FunctionTool(
            name=fn["name"],
            description=fn.get("description") or fn["name"],
            params_json_schema=fn.get("parameters") or {"type": "object", "properties": {}},
            on_invoke_tool=invoke,
            strict_json_schema=False,
            tool_input_guardrails=[policy_gate] if engineered else [],
        )

    class RecordModelCalls(RunHooks):
        async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
            text, calls = [], []
            for item in response.output:
                if getattr(item, "type", None) == "function_call":
                    calls.append({"id": item.call_id, "name": item.name, "arguments": parse_arguments(item.arguments)})
                elif getattr(item, "type", None) == "message":
                    text += [part.text for part in item.content if getattr(part, "text", None)]
            usage = response.usage
            details = getattr(usage, "input_tokens_details", None)
            recorder.model_step(
                "\n".join(text),
                calls,
                {
                    "prompt_tokens": usage.input_tokens,
                    "completion_tokens": usage.output_tokens,
                    "cached_tokens": getattr(details, "cached_tokens", 0) or 0,
                },
            )

    def stop_when_conversation_ends(context: Any, results: Any) -> ToolsToFinalOutputResult:
        return ToolsToFinalOutputResult(is_final_output=bool(runtime.ended), final_output="" if runtime.ended else None)

    if model_impl is None and not model.startswith("openai/"):
        raise ValueError("The Agents SDK retail agent uses OpenAI's client; pass an openai/... model")
    llm: Any = model_impl or model.removeprefix("openai/")
    agent = Agent(
        name="retail-support" if engineered else "retail-support-plain",
        instructions=instructions,
        model=llm,
        model_settings=ModelSettings(
            parallel_tool_calls=False if engineered else None,
            reasoning=Reasoning(effort=reasoning_effort) if reasoning_effort else None,
        ),
        tools=[domain_tool(schema) for schema in tools],
        tool_use_behavior=stop_when_conversation_ends,
    )

    items: list[Any] = [
        {"role": "assistant", "content": FIRST_AGENT_MESSAGE},
        {"role": "user", "content": first_message},
    ]
    hooks = RecordModelCalls()
    while not runtime.ended:
        try:
            result = await Runner.run(agent, items, hooks=hooks, max_turns=MAX_TURNS_PER_REPLY)
        except MaxTurnsExceeded:
            await runtime.end("max_steps")
            break
        items = result.to_input_list()
        if runtime.ended:
            break
        reply_text = str(result.final_output or "").strip()
        if not reply_text:
            await runtime.end("agent_error")
            break
        customer = await runtime.say(reply_text)
        if customer:
            items.append({"role": "user", "content": customer})
            recorder.customer(customer)
            guards.on_customer_message(customer)
    return items
