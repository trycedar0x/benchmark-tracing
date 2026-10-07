"""Trace a small agent with the benchtrace SDK. Runs offline: the "model" is a stub.

    uv run benchtrace serve                      # in another terminal
    uv run python examples/sdk_trace_agent.py
    uv run benchtrace trace <trace-id> --trace-id

Swap `fake_model` for a real client call; the spans stay the same.
"""

from __future__ import annotations

import os

from benchtrace.sdk import Benchtrace

QUESTIONS = {"q-1": "What is 17 * 23?", "q-2": "What is 1024 / 0?"}


def fake_model(prompt: str) -> dict:
    """Stands in for an LLM call. Asks for the calculator, then answers with its result."""
    if "Tool result:" in prompt:
        return {"text": "The answer is " + prompt.rsplit("Tool result:", 1)[1].strip(), "in": 40, "out": 8}
    expression = prompt.removeprefix("What is ").rstrip("?")
    return {"tool": "calculator", "arguments": {"expression": expression}, "in": 25, "out": 12}


def calculator(expression: str) -> str:
    return str(eval(expression, {"__builtins__": {}}))  # demo only; never eval untrusted input


def solve(bt: Benchtrace, task_id: str, question: str) -> str:
    # One trace per task. Extra keyword arguments become span attributes.
    with bt.trace("solve-question", task_id=task_id):
        prompt = question
        for _ in range(3):
            # Use GenAI semantic-convention names so model and token counts show up in benchtrace.
            with bt.span("call-model", kind="model", **{"gen_ai.request.model": "stub-model"}) as span:
                bt.record(span, "prompt", prompt)
                reply = fake_model(prompt)
                span.set_attribute("gen_ai.usage.input_tokens", reply["in"])
                span.set_attribute("gen_ai.usage.output_tokens", reply["out"])
                bt.record(span, "output", reply.get("text") or reply)
            if "tool" not in reply:
                return reply["text"]
            with bt.span(reply["tool"], kind="tool") as span:
                bt.record(span, "arguments", reply["arguments"])
                result = calculator(**reply["arguments"])  # an exception marks this span and the trace as errors
                bt.record(span, "result", result)
            prompt = f"{question}\nTool result: {result}"
        raise RuntimeError("Gave up after 3 steps")


def main() -> None:
    bt = Benchtrace(
        os.environ.get("BENCHTRACE_URL", "http://127.0.0.1:8321"),
        content="full",  # the server's BENCHTRACE_INGEST_CONTENT can still lower this
    )
    for task_id, question in QUESTIONS.items():
        try:
            print(task_id, "→", solve(bt, task_id, question))
        except Exception as ex:  # noqa: BLE001
            print(task_id, "→ error:", ex)

    health = bt.flush()  # waits until spooled batches are delivered, or the timeout passes
    for trace_id, receipt in health.receipts.items():
        print(f"trace {trace_id}: {receipt['state']} ({receipt['received_spans']}/{receipt['expected_spans']} spans)")
    if health.pending_batches:
        print(f"{health.pending_batches} batch(es) still spooled ({health.last_error}); they are sent next time.")


if __name__ == "__main__":
    main()
