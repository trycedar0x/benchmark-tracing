import asyncio
import copy
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from test_agent_transports import CASE, SECRET, local_server, response

from everybench.db import Base, Span, Trace
from everybench.evaluation import traces
from everybench.evaluation.traces import CallbackTraceSource, HTTPTraceSource, StoreTraceSource
from everybench.evaluation.types import TraceAccessError, TraceEvent, TraceEvidence

ROOT_ATTRS = {
    "everybench.run_id": CASE.run_id,
    "everybench.sample_id": CASE.sample_id,
    "everybench.attempt": CASE.attempt,
    "openinference.span.kind": "AGENT",
}


def payload():
    spans = [
        {
            "trace_id": CASE.trace_id,
            "span_id": "1" * 16,
            "parent_id": CASE.parent_span_id,
            "kind": "span",
            "name": "actual agent request",
            "attributes": ROOT_ATTRS,
            "content_state": "missing",
            "content": None,
        },
        {
            "trace_id": CASE.trace_id,
            "span_id": "2" * 16,
            "parent_id": "1" * 16,
            "kind": "span",
            "name": "actual model step",
            "attributes": {"gen_ai.operation.name": "chat"},
            "content_state": "present",
            "content": {
                "gen_ai.output.messages": [
                    {"role": "assistant", "parts": [{"type": "text", "content": "Look up revenue."}]}
                ]
            },
        },
        {
            "trace_id": CASE.trace_id,
            "span_id": "3" * 16,
            "parent_id": "2" * 16,
            "kind": "span",
            "name": "SEC observations",
            "attributes": {"openinference.span.kind": "TOOL"},
            "content_state": "present",
            "content": {"gen_ai.tool.call.result": {"revenue": 100}},
        },
    ]
    return {
        "trace": {
            "trace_id": CASE.trace_id,
            "workspace_id": "workspace-1",
            "run_id": None,
            "sample_id": None,
            "state": "closed",
            "span_count": 3,
            "attributes": {"expected_spans": 3, "sealed_at": "2026-01-01T12:00:00+00:00"},
        },
        "spans": copy.deepcopy(spans),
    }


def callback_evidence():
    p = payload()
    return TraceEvidence(
        trace_id=CASE.trace_id,
        state="complete",
        events=[
            TraceEvent(
                id=s["span_id"],
                parent_id=s["parent_id"],
                kind=s["kind"],
                name=s["name"],
                attributes=s["attributes"],
                content=s["content"],
            )
            for s in p["spans"]
        ],
    )


def get_callback(evidence):
    return asyncio.run(CallbackTraceSource(lambda trace_id, case: evidence).get_trace(CASE.trace_id, case=CASE))


def test_callback_authorizes_external_completion_but_validates_root_and_events():
    result = get_callback(callback_evidence())
    assert result.state == "complete"
    assert [event.kind for event in result.events] == ["agent", "model", "tool"]
    assert result.metadata["completeness_basis"] == "trusted_callback_declaration"
    assert result.run_id == CASE.run_id and result.sample_id == CASE.sample_id and result.attempt == CASE.attempt

    async def callback(trace_id, case):
        assert trace_id == CASE.trace_id and case is CASE
        return callback_evidence()

    assert asyncio.run(CallbackTraceSource(callback).get_trace(CASE.trace_id, case=CASE)).state == "complete"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda e: setattr(e, "trace_id", "d" * 32),
        lambda e: setattr(e, "run_id", "wrong"),
        lambda e: setattr(e, "sample_id", "wrong"),
        lambda e: setattr(e, "attempt", 3),
        lambda e: e.events[0].attributes.pop("everybench.run_id"),
        lambda e: e.events[0].attributes.update({"everybench.sample_id": "wrong"}),
        lambda e: e.events[0].attributes.update({"everybench.attempt": True}),
        lambda e: e.events[1].attributes.update({"everybench.run_id": "judge-run"}),
        lambda e: e.events[0].attributes.update({"openinference.span.kind": "LLM"}),
        lambda e: setattr(e.events[0], "parent_id", "unknown-parent"),
        lambda e: setattr(e.events[1], "parent_id", None),
        lambda e: setattr(e.events[1], "parent_id", e.events[2].id),
        lambda e: e.events.append(e.events[1]),
        lambda e: setattr(e.events[1], "id", ""),
        lambda e: setattr(e.events[1], "start_time", "not-a-time"),
        lambda e: e.metadata.update({"expected_spans": 4}),
        lambda e: e.metadata.update({"expected_spans": True}),
        lambda e: e.metadata.update({"expected_spans": 0}),
        lambda e: e.metadata.update({"expected_spans": -1}),
        lambda e: e.metadata.update({"expected_spans": "3"}),
        lambda e: e.metadata.update({"expected_spans": 3.0}),
        lambda e: e.metadata.update({"sealed_at": "2026-01-01"}),
    ],
)
def test_callback_rejects_wrong_binding_malformed_roots_duplicates_cycles_and_bad_seals(mutation):
    evidence = callback_evidence()
    mutation(evidence)
    with pytest.raises(TraceAccessError):
        get_callback(evidence)


@pytest.mark.parametrize("state", ["missing", "pending"])
def test_missing_pending_and_wrong_requested_trace_remain_explicit(state):
    evidence = TraceEvidence(trace_id=CASE.trace_id, state=state)
    assert get_callback(evidence).state == state
    with pytest.raises(TraceAccessError):
        asyncio.run(CallbackTraceSource(lambda trace_id, case: evidence).get_trace("d" * 32, case=CASE))


def test_callback_scorer_and_judge_descendants_are_never_agent_evidence():
    evidence = callback_evidence()
    evidence.events.extend(
        [
            TraceEvent(id="judge", parent_id=evidence.events[0].id, kind="scorer", name="finance grader"),
            TraceEvent(
                id="judge-model",
                parent_id="judge",
                kind="model",
                name="judge model",
                content={"output.value": "EVALUATOR EVIDENCE MUST NOT BE USED"},
            ),
        ]
    )
    assert [event.id for event in get_callback(evidence).events] == [event.id for event in evidence.events[:3]]
    evidence.events = evidence.events[:1] + evidence.events[3:]
    assert get_callback(evidence).state == "withheld"


def test_missing_and_withheld_content_cannot_become_complete_from_timestamps():
    evidence = callback_evidence()
    evidence.events[1].content = None
    evidence.events[1].end_time = "2026-01-01T12:00:00+00:00"
    assert get_callback(evidence).state == "incomplete"
    evidence.events[1].attributes["everybench.content_state"] = "withheld"
    assert get_callback(evidence).state == "withheld"
    evidence.events = evidence.events[:1]
    assert get_callback(evidence).state == "withheld"


@pytest.mark.parametrize("error_type", [RuntimeError, TraceAccessError])
def test_callback_seal_if_supplied_is_checked_and_failures_are_sanitized(error_type):
    evidence = callback_evidence()
    evidence.metadata.update({"expected_spans": 3, "sealed_at": "2026-01-01T12:00:00+00:00"})
    assert get_callback(evidence).metadata["completeness_basis"] == "producer_seal"

    def fail(trace_id, case):
        raise error_type(SECRET)

    source = CallbackTraceSource(fail)
    with pytest.raises(TraceAccessError) as failure:
        asyncio.run(source.get_trace(CASE.trace_id, case=CASE))
    assert SECRET not in repr(source) + json.dumps(source.describe()) + str(failure.value)


@pytest.fixture
def local_store(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            with session.begin():
                yield session

    monkeypatch.setattr(traces, "session_scope", scope)
    p = payload()
    with scope() as session:
        session.add(Trace(**p["trace"]))
        for raw in p["spans"]:
            session.add(Span(workspace_id="workspace-1", **raw))
    yield scope
    engine.dispose()


def test_store_reuses_sealed_completeness_and_checks_workspace_per_span(local_store):
    source = StoreTraceSource(workspace_id="workspace-1")
    assert asyncio.run(source.get_trace(CASE.trace_id, case=CASE)).state == "complete"
    with pytest.raises(TraceAccessError):
        asyncio.run(StoreTraceSource(workspace_id="wrong").get_trace(CASE.trace_id, case=CASE))
    with pytest.raises(TraceAccessError):
        asyncio.run(StoreTraceSource().get_trace(CASE.trace_id, case=CASE))
    with local_store() as session:
        span = session.query(Span).filter(Span.kind == "span", Span.span_id == "3" * 16).one()
        span.workspace_id = "wrong"
    with pytest.raises(TraceAccessError):
        asyncio.run(source.get_trace(CASE.trace_id, case=CASE))


def test_store_closed_without_seal_is_not_complete(local_store):
    with local_store() as session:
        trace = session.get(Trace, CASE.trace_id)
        trace.attributes = {}
    result = asyncio.run(StoreTraceSource(workspace_id="workspace-1").get_trace(CASE.trace_id, case=CASE))
    assert result.state == "incomplete"
    assert result.metadata["completeness_basis"] == "unsealed"


def test_store_missing_pending_withheld_and_incomplete(local_store):
    source = StoreTraceSource(workspace_id="workspace-1")
    with local_store() as session:
        session.query(Span).filter(Span.span_id == "2" * 16).one().content_state = "withheld"
    result = asyncio.run(source.get_trace(CASE.trace_id, case=CASE))
    assert result.state == "withheld" and result.events[1].content is None
    with local_store() as session:
        session.get(Trace, CASE.trace_id).state = "incomplete"
    assert asyncio.run(source.get_trace(CASE.trace_id, case=CASE)).state == "incomplete"
    with local_store() as session:
        session.query(Span).delete()
        trace = session.get(Trace, CASE.trace_id)
        trace.state, trace.span_count = "open", 0
    assert asyncio.run(source.get_trace(CASE.trace_id, case=CASE)).state == "pending"
    with local_store() as session:
        session.query(Trace).delete()
    assert asyncio.run(source.get_trace(CASE.trace_id, case=CASE)).state == "missing"


@pytest.mark.parametrize("bad_span_workspace", [False, True])
def test_http_source_default_workspace_uses_authorized_remote_scope_not_local_storage(bad_span_workspace):
    async def run():
        p = payload()
        for span in p["spans"]:
            span["workspace_id"] = "workspace-1"
        if bad_span_workspace:
            p["spans"][1]["workspace_id"] = "wrong"

        async def handler(request, reader, writer):
            writer.write(response(json.dumps(p).encode(), content_type="application/json"))
            await writer.drain()

        async with local_server(handler) as (url, requests):
            source = HTTPTraceSource(url, api_key="hosted-workspace-credential")
            if bad_span_workspace:
                with pytest.raises(TraceAccessError):
                    await source.get_trace(CASE.trace_id, case=CASE)
            else:
                result = await source.get_trace(CASE.trace_id, case=CASE)
                assert result.state == "complete" and result.metadata["workspace_id"] == "workspace-1"
            assert "x-everybench-workspace" not in requests[0]["headers"]
            assert source.describe()["workspace_id"] is None  # Do not mutate local storage configuration.

    asyncio.run(run())


def test_http_source_uses_distinct_auth_workspace_and_real_get_schema():
    async def run():
        async def handler(request, reader, writer):
            writer.write(response(json.dumps(payload()).encode(), content_type="application/json"))
            await writer.drain()

        async with local_server(handler) as (url, requests):
            source = HTTPTraceSource(url, api_key="separate-trace-credential", workspace_id="workspace-1")
            result = await source.get_trace(CASE.trace_id, case=CASE)
            assert result.state == "complete"
            assert requests[0]["line"].startswith(f"GET /api/traces/{CASE.trace_id} ")
            assert requests[0]["headers"]["authorization"] == "Bearer separate-trace-credential"
            assert requests[0]["headers"]["x-everybench-workspace"] == "workspace-1"
            assert "separate-trace-credential" not in repr(source) + json.dumps(source.describe())

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["trace"].update({"workspace_id": "wrong"}),
        lambda p: p["trace"].update({"trace_id": "d" * 32}),
        lambda p: p["trace"].update({"run_id": "wrong"}),
        lambda p: p["trace"].update({"sample_id": "wrong"}),
        lambda p: p["trace"].update({"span_count": 4}),
        lambda p: p["trace"].pop("span_count"),
        lambda p: p["trace"]["attributes"].pop("expected_spans"),
        lambda p: p["trace"]["attributes"].update({"expected_spans": 4}),
        lambda p: p["trace"]["attributes"].update({"everybench.attempt": 2.0}),
        lambda p: p["trace"]["attributes"].update({"everybench.run_id": "wrong"}),
        lambda p: p["spans"][0].update({"trace_id": "d" * 32}),
        lambda p: p["spans"][0].update({"workspace_id": "wrong"}),
        lambda p: p["spans"][0].update({"run_id": "wrong"}),
        lambda p: p["spans"][0]["attributes"].pop("everybench.attempt"),
        lambda p: p["spans"][1].update({"content_state": "arbitrary"}),
    ],
)
def test_http_source_fails_closed_on_wrong_ownership_and_malformed_payload(mutation):
    async def run():
        p = payload()
        mutation(p)

        async def handler(request, reader, writer):
            writer.write(response(json.dumps(p).encode(), content_type="application/json"))
            await writer.drain()

        async with local_server(handler) as (url, requests):
            with pytest.raises(TraceAccessError):
                await HTTPTraceSource(url, workspace_id="workspace-1").get_trace(CASE.trace_id, case=CASE)
            assert len(requests) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,state",
    [
        ("404 Not Found", "missing"),
        ("401 Unauthorized", None),
        ("302 Found", None),
        ("500 Internal Server Error", None),
    ],
)
def test_http_trace_missing_error_and_redirect_never_forward_credentials(status, state):
    async def run():
        async def handler(request, reader, writer):
            writer.write(
                response(
                    SECRET.encode(),
                    status=status,
                    content_type="application/json",
                    headers="Location: /must-not-follow\r\n",
                )
            )
            await writer.drain()

        async with local_server(handler) as (url, requests):
            source = HTTPTraceSource(url, api_key=SECRET)
            if state:
                assert (await source.get_trace(CASE.trace_id, case=CASE)).state == state
            else:
                with pytest.raises(TraceAccessError) as failure:
                    await source.get_trace(CASE.trace_id, case=CASE)
                assert SECRET not in str(failure.value)
            assert len(requests) == 1

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [True, False])
def test_http_trace_timeout_and_cancel_close_connection(cancel):
    async def run():
        started, closed = asyncio.Event(), asyncio.Event()

        async def handler(request, reader, writer):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{")
            await writer.drain()
            started.set()
            try:
                await reader.read()
            finally:
                closed.set()  # EOF and TCP reset both confirm connection closure.

        async with local_server(handler) as (url, requests):
            source = HTTPTraceSource(url, timeout=1 if cancel else 0.1)
            task = asyncio.create_task(source.get_trace(CASE.trace_id, case=CASE))
            await asyncio.wait_for(started.wait(), 1)
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else TraceAccessError):
                await task
            await asyncio.wait_for(closed.wait(), 1)
            assert len(requests) == 1

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["scorer", "evaluator", "judge"])
@pytest.mark.parametrize("attrs", [{"gen_ai.operation.name": "chat"}, {"openinference.span.kind": "LLM"}])
def test_callback_explicit_evaluator_kind_survives_semantic_normalization(kind, attrs):
    e = callback_evidence()
    e.events.extend(
        [
            TraceEvent(
                id="grader",
                parent_id=e.events[0].id,
                kind=kind,
                name="grader",
                attributes=attrs,
                content={"output.value": "PRIVATE EVALUATOR"},
            ),
            TraceEvent(id="child", parent_id="grader", kind="model", name="judge", content={"output.value": "PRIVATE"}),
            TraceEvent(
                id="grandchild", parent_id="child", kind="tool", name="judge tool", content={"tool.result": "PRIVATE"}
            ),
        ]
    )
    result = get_callback(e)
    assert result.state == "complete" and len(result.events) == 3


def test_sealed_filtered_callback_evidence_revalidation_preserves_raw_producer_count():
    e = callback_evidence()
    e.events.extend(
        [
            TraceEvent(id="grader", parent_id=e.events[0].id, kind="scorer", name="grader"),
            TraceEvent(id="judge", parent_id="grader", kind="model", name="judge", content={"output.value": "PRIVATE"}),
        ]
    )
    e.metadata.update({"expected_spans": 5, "sealed_at": "2026-01-01T12:00:00+00:00"})
    result = get_callback(e)
    for _ in range(3):
        result = traces._validate_evidence(result, CASE, trusted_callback=True)
        assert result.state == "complete" and len(result.events) == 3
        assert result.metadata["expected_spans"] == 5
        assert result.metadata["completeness_basis"] == "producer_seal"
    # Serialized metadata is never proof that excluded/missing raw spans arrived.
    forged = TraceEvidence.model_validate(result.model_dump())
    forged.metadata.update({"raw_verified": True, "raw_span_count": 5, "completeness_basis": "producer_seal"})
    with pytest.raises(TraceAccessError, match="missing producer"):
        traces._validate_evidence(forged, CASE, trusted_callback=True)
    result.events.pop()
    with pytest.raises(TraceAccessError, match="missing producer"):
        traces._validate_evidence(result, CASE, trusted_callback=True)


@pytest.mark.parametrize("on_root", [False, True])
def test_callback_producer_withheld_marker_is_not_masked_by_retained_input_or_seal(on_root):
    e = callback_evidence()
    e.events[0 if on_root else 1].attributes.update(
        {"everybench.evidence.withheld": True, "everybench.evidence.withheld_keys": ["output.value"]}
    )
    e.events[1].content = {"input.value": "Visible retained input"}
    e.metadata.update({"expected_spans": 3, "sealed_at": "2026-01-01T12:00:00+00:00"})
    result = get_callback(e)
    assert result.state == "withheld" and result.metadata["expected_spans"] == 3
