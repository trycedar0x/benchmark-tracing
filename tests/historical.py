"""Stored historical model-run fixtures; never invoke a model or retired scheduler."""

from inspect_ai.log import EvalSample
from inspect_ai.model import ModelOutput

from everybench.db import Run, now, session_scope
from everybench.ingest import store_sample
from everybench.inspect_convert import convert_sample


def historical_run(*, benchmark="toy-arith", model="mock/strong", limit=None, content_policy="full", workspace_id=None):
    count = limit or 40
    with session_scope() as session:
        run = Run(
            benchmark=benchmark,
            variant_key=benchmark + "-historical-v1",
            model=model,
            status="succeeded",
            content_policy=content_policy,
            workspace_id=workspace_id,
            samples_total=count,
            started_at=now(),
            finished_at=now(),
            manifest={
                "benchmark": {"task": "everybench/toy_arith", "license": "Apache-2.0"},
                "versions": {"inspect_ai": "historical-fixture"},
            },
        )
        session.add(run)
        session.flush()
        for index in range(count):
            correct = index < (12 if model == "mock/weak" else 32)
            error = model == "mock/flaky" and index % 10 == 0
            if model == "mock/flaky" and index % 5 == 1:
                correct = False
            answer = "4" if correct else "5"
            sample = EvalSample(
                id=f"sample-{index}",
                epoch=1,
                input="What is 2 + 2?",
                target="4",
                metadata={},
                scores={"match": {"value": int(correct)}},
                output=ModelOutput.from_content(model, answer),
            )
            converted = convert_sample(sample, run.id)
            trace_id = converted["trace"]["trace_id"]

            def span(id_, kind, content, status="ok"):
                return {"span_id": id_, "name": kind, "kind": kind, "content": content, "status": status}

            converted["spans"] = [
                span("model-call", "model", {"tool_calls": [{"function": "calculator", "arguments": {"x": "2+2"}}]}),
                span("tool-result", "tool", {"arguments": {"x": "2+2"}, "result": "4"}),
                span("model-answer", "model", {"output": answer}),
            ]
            if error:
                converted["result"].update(outcome="error", score=None, error="Historical provider failure")
                converted["spans"] = [span("error", "error", {"error": "Historical provider failure"}, "error")]
            assert converted["result"]["trace_id"] == trace_id
            store_sample(session, run, converted)
        run.resolved_models = [model + "-revision-1"]
        if model == "mock/drifty":
            run.resolved_models.append(model + "-revision-2")
        session.flush()
    return run
