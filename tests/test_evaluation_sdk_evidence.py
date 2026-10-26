import hashlib

import pytest
from sqlalchemy import select
from test_evaluation_engine import SyntheticBenchmark
from test_sdk import LiveServer, free_port

from everybench.db import Run, Span, Trace, session_scope
from everybench.evaluation import StoreTraceSource, TraceEvent, TraceEvidence, evaluate_sync
from everybench.evaluation.evidence import TOOL_ARGUMENT_CAP, TOOL_RESULT_CAP, format_evidence, visible_content
from everybench.sdk import EveryBench


def test_native_sdk_invocation_record_seal_and_local_store_are_real_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("EVERYBENCH_INGEST_CONTENT", "full")
    with LiveServer(free_port()) as server:
        client = EveryBench(
            server.url, spool_dir=tmp_path / "spool", set_global=False, content="full", flush_interval=0.05
        )
        try:

            def agent(case):
                with client.invocation(case):
                    with client.span("Actual model", kind="model", **{"gen_ai.response.model": "actual-v2"}) as span:
                        client.record(span, "input", "Captured request")
                        client.record(span, "output", "Captured response")
                    with client.span("Actual tool", kind="tool") as span:
                        client.record(span, "tool_arguments", {"company": "example"})
                        client.record(span, "tool_result", {"revenue": 123})
                return "submitted answer"

            benchmark = SyntheticBenchmark(requires_trace=True)
            report = evaluate_sync(
                benchmark,
                agent,
                trace_source=StoreTraceSource(),
                allow_paid=True,
                limit=1,
                trace_timeout=5,
                content_policy="full",
            )
            assert report.results[0].status == "scored", report.model_dump_json()
            evidence = benchmark.graded[0][2]
            text, _ = format_evidence(evidence)
            assert "Captured response" in text and '"revenue": 123' in text
            health = client.flush(timeout=5)
            assert health.receipts[evidence.trace_id]["expected_spans"] == 3
            with session_scope() as session:
                trace = session.get(Trace, evidence.trace_id)
                assert trace.run_id == report.run_id and trace.sample_id == "original/0"
                spans = list(session.scalars(select(Span).where(Span.trace_id == trace.trace_id)))
                assert len(spans) == 3 and all(span.run_id == report.run_id for span in spans)
                assert session.get(Run, report.run_id).resolved_models == ["actual-v2"]
        finally:
            client.close(timeout=5)


def test_sdk_aliases_share_reasoning_filters_and_do_not_accept_arbitrary_records():
    event = TraceEvent(
        id="model",
        name="actual",
        kind="model",
        content={
            "everybench.content.input": [{"role": "user", "content": "Question"}],
            "everybench.content.output": {"text": "Visible answer", "reasoning": "HIDDEN PROVIDER TEXT"},
            "everybench.content.reasoning": "HIDDEN REASONING",
            "everybench.content.private": "HIDDEN PRIVATE RECORD",
        },
    )
    visible = visible_content(event)
    assert set(visible) == {"input.value", "output.value"}
    text, cut = format_evidence(TraceEvidence(trace_id="a" * 32, state="complete", events=[event]))
    assert "Visible answer" in text and "Question" in text and "HIDDEN" not in text and not cut
    event.attributes["everybench.content_state"] = "withheld"
    assert visible_content(event) == {}


@pytest.mark.parametrize(
    "alias,canonical,cap",
    [
        ("tool_arguments", "tool.arguments", TOOL_ARGUMENT_CAP),
        ("tool_result", "tool.result", TOOL_RESULT_CAP),
    ],
)
def test_sdk_tool_aliases_share_caps_and_original_content_hash(alias, canonical, cap):
    value = "body" * (cap + 10)
    event = TraceEvent(id="tool", name="actual", kind="tool", content={f"everybench.content.{alias}": value})
    evidence = TraceEvidence(trace_id="a" * 32, state="complete", events=[event])
    text, cut = format_evidence(evidence)
    assert cut and canonical in text and hashlib.sha256(value.encode()).hexdigest() in text
    assert len(text) < cap + 200


@pytest.mark.parametrize("receiver_policy", ["metadata", "redacted", "full"])
def test_producer_withheld_metadata_survives_receiver_policy_and_seal(tmp_path, monkeypatch, receiver_policy):
    monkeypatch.setenv("EVERYBENCH_INGEST_CONTENT", receiver_policy)
    with LiveServer(free_port()) as server:
        client = EveryBench(server.url, spool_dir=tmp_path / "spool", set_global=False, content="full")
        try:

            def agent(case):
                with client.invocation(case):
                    with client.span(
                        "Partial model capture",
                        kind="model",
                        **{
                            "everybench.evidence.withheld": True,
                            "everybench.evidence.withheld_keys": ["gen_ai.output.messages"],
                        },
                    ) as span:
                        client.record(span, "input", "Retained input, not omitted output")
                return "answer"

            benchmark = SyntheticBenchmark(requires_trace=True)
            report = evaluate_sync(
                benchmark,
                agent,
                trace_source=StoreTraceSource(),
                allow_paid=True,
                limit=1,
                trace_timeout=5,
                content_policy="full",
            )
            result = report.results[0]
            assert result.status == "trace_missing" and result.trace_state == "withheld"
            assert not benchmark.graded
            assert client.flush(timeout=5).receipts[result.trace_id]["expected_spans"] == 2
            with session_scope() as session:
                trace = session.get(Trace, result.trace_id)
                assert trace.attributes["expected_spans"] == 2 and trace.attributes["sealed_at"]
                model = session.scalar(select(Span).where(Span.trace_id == trace.trace_id, Span.kind == "model"))
                assert model.attributes["everybench.evidence.withheld"] is True
                assert model.attributes["everybench.evidence.withheld_keys"] == ["gen_ai.output.messages"]
                assert "Retained input" not in str(model.attributes)
                if receiver_policy == "metadata":
                    assert model.content is None
                elif receiver_policy == "full":
                    assert "Retained input" in str(model.content)
        finally:
            client.close(timeout=5)
