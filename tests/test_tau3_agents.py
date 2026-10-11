"""The tau3-bench example agents against a scripted benchmark runtime and scripted models (offline).

The guard and baseline tests need nothing extra. The framework agents' tests need the packages
the agents install in the task container; run them with:

    uv run --isolated --no-project --with-editable . --with pytest \\
        --with-requirements examples/tau3_agents/requirements.txt pytest tests/test_tau3_agents.py
"""

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).parents[1] / "examples" / "tau3_agents"
sys.path.insert(0, str(AGENTS_DIR))  # the container runs these modules side by side, as top-level imports
import guards  # noqa: E402
import runner  # noqa: E402


class FakeRuntime:
    """Stands in for the tau3-runtime MCP server: scripted customer turns and tool results."""

    def __init__(self, customer: list[str], tools: dict[str, str]):
        self.customer = list(customer)
        self.tools = tools
        self.submitted: list[str] = []  # tool names the benchmark actually saw
        self.sent: list[str] = []  # messages the customer actually saw

    async def call_tool(self, name, args, read_timeout_seconds=None):
        if name == "start_conversation":
            return self._text(self.customer.pop(0))
        if name == "submit_assistant_tool_calls":
            calls = json.loads(args["tool_calls_json"])
            self.submitted += [c["name"] for c in calls]
            results = [{"id": c["id"], "content": self.tools.get(c["name"], "ok")} for c in calls]
            transfer = any(c["name"] == "transfer_to_human_agents" for c in calls)
            return self._json({"tool_results": results, **({"termination_reason": "transfer"} if transfer else {})})
        if name == "submit_assistant_message":
            self.sent.append(args["message"])
            if not self.customer:
                return self._json({"observation": "###STOP###", "termination_reason": "user_stop"})
            return self._json({"observation": self.customer.pop(0)})
        return self._json({})

    @staticmethod
    def _text(text):
        return {"content": [{"type": "text", "text": text}]}

    def _json(self, payload):
        return self._text(json.dumps(payload))


def start(customer, tools):
    fake = FakeRuntime(customer, tools)
    runtime = runner.Tau3Runtime(fake, 5)
    first = asyncio.run(runtime.start(1, 50, 5))
    recorder = runner.Recorder(agent_name="test", model="openai/gpt-test")
    recorder.customer(first)
    return fake, runtime, recorder, first


def tool_schemas(*names):
    params = {"type": "object", "properties": {}}
    return [{"type": "function", "function": {"name": n, "description": n, "parameters": params}} for n in names]


def guard_names(recorder):
    return [g["name"] for s in recorder.steps for g in (s.get("extra") or {}).get("guards", [])]


# ---------------------------------------------------------------------------- guards


def test_retail_guards_authenticate_then_confirm_once():
    g = guards.RetailGuards()
    g.on_customer_message("Cancel #W1 please, I'm a@b.c")
    assert g.tool_call("get_order_details", {}).guard == "authenticate_first"
    assert not g.tool_call("find_user_id_by_email", {}).blocked
    g.on_tool_result("find_user_id_by_email", {}, "Error: user not found")
    assert g.user_id is None
    g.on_tool_result("find_user_id_by_email", {}, "ana_1")
    assert not g.tool_call("get_order_details", {}).blocked
    assert g.tool_call("cancel_pending_order", {}).guard == "confirm_first"
    g.on_customer_message("Yes, please cancel it.")
    assert not g.tool_call("cancel_pending_order", {}).blocked
    g.on_tool_result("cancel_pending_order", {}, "cancelled")
    assert g.tool_call("cancel_pending_order", {}).guard == "confirm_first"  # one change per confirmation
    g.on_customer_message("Yes, but actually I'd rather keep the shoes and only return the jacket from that order.")
    assert g.tool_call("return_delivered_order_items", {}).blocked  # a hedged yes is not a confirmation


def test_banking_guards_hold_once_and_keep_case_notes():
    g = guards.BankingGuards()
    g.on_customer_message("Close my card.")
    assert g.reply("Sorry, I'm unable to help with that.").guard == "search_before_giving_up"
    assert g.tool_call("transfer_to_human_agents", {}).guard == "search_before_transfer"
    assert g.tool_call("call_discoverable_agent_tool", {}).guard == "verify_identity"
    assert not g.tool_call("call_discoverable_agent_tool", {}).blocked  # a speed bump, not a wall
    g.on_tool_result("KB_search", {"query": "close card"}, "Doc 12")
    assert not g.reply("I can't help with that, it isn't offered.").blocked
    g.on_tool_result("log_verification", {"name": "Ana", "user_id": "u1"}, "Verification logged successfully.")
    unlocked = "Tool unlocked: x\nDescription: Close a card"
    g.on_tool_result("unlock_discoverable_agent_tool", {"agent_tool_name": "close_7834"}, unlocked)
    notes = g.case_notes()
    assert "Identity verified and logged: Ana (user u1)" in notes and "close_7834: Close a card" in notes


def test_guards_fail_open_after_too_many_blocks():
    g = guards.RetailGuards()
    verdicts = [g.tool_call("get_order_details", {}) for _ in range(guards.MAX_BLOCKS + 1)]
    assert all(v.blocked for v in verdicts[:-1]) and not verdicts[-1].blocked


def test_runtime_errors_are_not_mistaken_for_the_customer():
    class FailingRuntime(FakeRuntime):
        async def call_tool(self, name, args, read_timeout_seconds=None):
            if name == "start_conversation":
                return {"content": [{"type": "text", "text": "AuthenticationError: bad key"}], "isError": True}
            return await super().call_tool(name, args, read_timeout_seconds)

    runtime = runner.Tau3Runtime(FailingRuntime([], {}), 5)
    with pytest.raises(RuntimeError, match="tau3 runtime: AuthenticationError"):
        asyncio.run(runtime.start(1, 50, 5))


# ---------------------------------------------------------------------------- trajectory


def test_trajectory_is_valid_atif_and_becomes_spans():
    recorder = runner.Recorder(agent_name="test", model="openai/gpt-test")
    recorder.customer("Cancel order #W1.")
    usage = {"prompt_tokens": 100, "completion_tokens": 10}
    recorder.model_step(
        "",
        [{"id": "c1", "name": "cancel_pending_order", "arguments": {"order_id": "#W1"}}],
        usage,
        "gpt-test-2026-01-01",
    )
    recorder.tool_result("c1", "ok")
    recorder.model_step("Done.", [], usage, "gpt-test-2026-01-01")
    trajectory = recorder.trajectory({"agent": "retail"})
    harbor_models = pytest.importorskip("harbor.models.trajectories")
    harbor_models.Trajectory.model_validate(trajectory)
    from benchtrace.harbor_adapter import _trajectory_spans

    spans, models, first_user, last_agent = _trajectory_spans(trajectory, "root", "t1", None, None)
    assert models == {"gpt-test-2026-01-01"}  # the model that actually answered
    assert first_user == "Cancel order #W1." and last_agent == "Done."
    tool = next(s for s in spans if s["kind"] == "tool")
    assert tool["content"] == {"arguments": {"order_id": "#W1"}, "result": "ok"}


def test_code_steps_are_not_model_calls():
    from benchtrace.harbor_adapter import _trajectory_spans

    recorder = runner.Recorder(agent_name="test", model="openai/gpt-test")
    recorder.code_step("research", [{"id": "r1", "name": "KB_search", "arguments": {"query": "close card"}}])
    recorder.tool_result("r1", "Doc 12")
    spans, models, *_ = _trajectory_spans(recorder.trajectory({}), "root", "t1", None, None)
    assert models == set()
    assert [(s["kind"], s["name"]) for s in spans] == [("span", "research"), ("tool", "KB_search")]


# ---------------------------------------------------------------------------- retail on the OpenAI Agents SDK


def test_retail_agent_guardrails_on_openai_agents_sdk():
    pytest.importorskip("agents")
    import retail_agent
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    cancel = {"order_id": "#W1", "reason": "no longer needed"}
    script = [
        ("call", "get_order_details", {"order_id": "#W1"}),  # blocked: not authenticated
        ("call", "find_user_id_by_email", {"email": "a@b.c"}),
        ("call", "cancel_pending_order", cancel),  # blocked: not confirmed
        ("say", "I will cancel #W1 (no longer needed). Reply yes to confirm."),
        ("call", "cancel_pending_order", cancel),
        ("say", "Cancelled."),
    ]
    seen_inputs = []

    class ScriptedModel(Model):
        async def get_response(self, system_instructions, input, *args, **kwargs):
            seen_inputs.append(json.dumps(input, default=str))
            kind, *rest = script.pop(0)
            if kind == "call":
                name, args = rest
                item = ResponseFunctionToolCall(
                    type="function_call", call_id=f"c{len(script)}", name=name, arguments=json.dumps(args)
                )
            else:
                text = ResponseOutputText(type="output_text", text=rest[0], annotations=[])
                item = ResponseOutputMessage(
                    id="m", type="message", role="assistant", status="completed", content=[text]
                )
            usage = Usage(requests=1, input_tokens=50, output_tokens=5)
            return ModelResponse(output=[item], usage=usage, response_id=None)

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError

    fake, runtime, recorder, first = start(
        ["Cancel order #W1, my email is a@b.c.", "Yes, please cancel it."], {"find_user_id_by_email": "ana_1"}
    )
    asyncio.run(
        retail_agent.run_retail_agent(
            runtime,
            recorder,
            model="openai/gpt-test",
            policy="p",
            tools=tool_schemas("find_user_id_by_email", "get_order_details", "cancel_pending_order"),
            first_message=first,
            model_impl=ScriptedModel(),
        )
    )
    assert guard_names(recorder) == ["authenticate_first", "confirm_first"]
    assert fake.submitted == ["find_user_id_by_email", "cancel_pending_order"]  # blocked calls never reached it
    assert fake.sent == ["I will cancel #W1 (no longer needed). Reply yes to confirm.", "Cancelled."]
    assert runtime.ended == "user_stop" and not script
    blocked = next(s for s in recorder.steps if (s.get("extra") or {}).get("guards"))
    assert blocked["observation"]["results"][0]["content"].startswith("Blocked: authenticate")
    assert "authenticate the customer first" in seen_inputs[1]  # the model saw the guardrail's reason


# ---------------------------------------------------------------------------- banking on LangGraph


def test_banking_agent_graph_on_langgraph():
    pytest.importorskip("langgraph")
    import banking_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    ids = iter(range(100))

    def call(tool, **args):
        return AIMessage("", tool_calls=[{"id": f"c{next(ids)}", "name": tool, "args": args}])

    script = iter(
        [
            call("unlock_discoverable_agent_tool", agent_tool_name="close_7834"),
            call("call_discoverable_agent_tool", agent_tool_name="close_7834"),  # held once: not verified
            call("log_verification", name="Ana", user_id="u1"),
            call("call_discoverable_agent_tool", agent_tool_name="close_7834"),
            AIMessage("Your card is closed."),
            call("transfer_to_human_agents"),
        ]
    )
    prompts = []

    class ScriptedChatModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages, *args, **kwargs):
            prompts.append(messages)
            return await super().ainvoke(messages, *args, **kwargs)

    tools = tool_schemas(
        "KB_search",
        "unlock_discoverable_agent_tool",
        "call_discoverable_agent_tool",
        "log_verification",
        "transfer_to_human_agents",
    )
    fake, runtime, recorder, first = start(
        ["Please close my credit card.", "Also, can I talk to a person?"],
        {
            "KB_search": "Doc 12: closing a card. Unlock close_7834.",
            "log_verification": "Verification logged successfully.",
            "unlock_discoverable_agent_tool": "Tool unlocked: close_7834\nDescription: Close a card",
        },
    )
    asyncio.run(
        banking_agent.run_banking_agent(
            runtime,
            recorder,
            model="openai/gpt-test",
            policy="p",
            tools=tools,
            first_message=first,
            chat_model=ScriptedChatModel(messages=script),
        )
    )
    # The research node searched the knowledge base for each customer message, so the transfer was
    # allowed; the only guard to fire was the identity hold.
    assert fake.submitted.count("KB_search") == 2 and fake.submitted.count("call_discoverable_agent_tool") == 1
    assert guard_names(recorder) == ["verify_identity"]
    assert fake.sent == ["Your card is closed."] and runtime.ended == "transfer"
    notes = [m.content for m in prompts[-1] if m.type == "system"][1]
    assert "Identity verified and logged: Ana (user u1)" in notes and "close_7834" in notes
    research = next(s for s in recorder.steps if (s.get("extra") or {}).get("code_step") == "research")
    assert research["llm_call_count"] == 0 and research["observation"]["results"][0]["content"].startswith("Doc 12")


def test_agent_pins_match_requirements_file():
    pinned = set(re.findall(r'"([a-z-]+==[\d.]+)"', (AGENTS_DIR / "harbor_agents.py").read_text()))
    lines = (AGENTS_DIR / "requirements.txt").read_text().splitlines()
    listed = {ln.split("#")[0].strip() for ln in lines if "==" in ln}
    assert pinned and pinned <= listed


# ---------------------------------------------------------------------------- plain twins


def test_plain_retail_twin_has_no_guardrails():
    pytest.importorskip("agents")
    import retail_agent
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    script = [("call", "cancel_pending_order", {"order_id": "#W1"}), ("say", "Cancelled.")]
    instructions = []

    class ScriptedModel(Model):
        async def get_response(self, system_instructions, input, *args, **kwargs):
            instructions.append(system_instructions)
            kind, *rest = script.pop(0)
            if kind == "call":
                item = ResponseFunctionToolCall(
                    type="function_call", call_id="c1", name=rest[0], arguments=json.dumps(rest[1])
                )
            else:
                text = ResponseOutputText(type="output_text", text=rest[0], annotations=[])
                item = ResponseOutputMessage(
                    id="m", type="message", role="assistant", status="completed", content=[text]
                )
            return ModelResponse(
                output=[item], usage=Usage(requests=1, input_tokens=5, output_tokens=1), response_id=None
            )

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError

    fake, runtime, recorder, first = start(["Cancel order #W1."], {})
    asyncio.run(
        retail_agent.run_retail_agent(
            runtime,
            recorder,
            model="openai/gpt-test",
            policy="p",
            tools=tool_schemas("cancel_pending_order"),
            first_message=first,
            engineered=False,
            model_impl=ScriptedModel(),
        )
    )
    # Unauthenticated and unconfirmed, the plain twin's write still reaches the benchmark.
    assert fake.submitted == ["cancel_pending_order"] and guard_names(recorder) == []
    assert instructions[0].startswith("<instructions>\nYou are a customer service agent")


def test_plain_banking_twin_is_a_model_tools_loop():
    pytest.importorskip("langgraph")
    import banking_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    script = iter(
        [
            AIMessage("", tool_calls=[{"id": "c1", "name": "call_discoverable_agent_tool", "args": {}}]),
            AIMessage("Sorry, I'm unable to help with that."),
        ]
    )

    class ScriptedChatModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            assert "parallel_tool_calls" not in kwargs  # framework defaults
            return self

    fake, runtime, recorder, first = start(["Close my card."], {})
    asyncio.run(
        banking_agent.run_banking_agent(
            runtime,
            recorder,
            model="openai/gpt-test",
            policy="p",
            tools=tool_schemas("KB_search", "call_discoverable_agent_tool"),
            first_message=first,
            engineered=False,
            chat_model=ScriptedChatModel(messages=script),
        )
    )
    # No research search, no identity hold, and the unsearched give-up goes straight to the customer.
    assert fake.submitted == ["call_discoverable_agent_tool"] and guard_names(recorder) == []
    assert fake.sent == ["Sorry, I'm unable to help with that."]
    assert not any((s.get("extra") or {}).get("code_step") for s in recorder.steps)
