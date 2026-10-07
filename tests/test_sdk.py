"""SDK and OTLP ingest against a live server on a local port."""

import json
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request

import pytest
import uvicorn
from sqlalchemy import select

from benchtrace.db import Span, Trace, session_scope
from benchtrace.sdk import Benchtrace
from benchtrace.server import create_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(uvicorn.Config(create_app(workers=0), port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def server():
    with LiveServer(free_port()) as s:
        yield s


def client(url: str, tmp_path, **kwargs) -> Benchtrace:
    return Benchtrace(url, spool_dir=tmp_path / "spool", set_global=False, flush_interval=0.05, **kwargs)


def emit(bt: Benchtrace, name: str = "task") -> str:
    with bt.trace(name, task_id="q1") as root:
        with bt.span("plan", kind="model", **{"gen_ai.request.model": "demo"}) as s:
            bt.record(s, "prompt", "What is 2+2? key sk-abcdefghijklmnopqrstuvwx")
            bt.record(s, "output", "4")
        with bt.span("calculator", kind="tool") as s:
            bt.record(s, "arguments", {"expression": "2+2"})
        trace_id = format(root.get_span_context().trace_id, "032x")
    return trace_id


def stored(trace_id: str) -> tuple[Trace, list[Span]]:
    with session_scope() as session:
        trace = session.get(Trace, trace_id)
        spans = session.scalars(select(Span).where(Span.trace_id == trace_id)).all()
    return trace, list(spans)


def test_sdk_delivers_and_seals_with_metadata_only_by_default(server, tmp_path):
    bt = client(server.url, tmp_path)
    trace_id = emit(bt)
    health = bt.close(timeout=10)
    assert health.pending_batches == 0 and health.rejected_batches == 0
    receipt = health.receipts[trace_id]
    assert receipt == {"trace_id": trace_id, "expected_spans": 3, "received_spans": 3, "state": "closed"}
    trace, spans = stored(trace_id)
    assert trace.state == "closed" and trace.name == "task" and trace.source == "sdk"
    assert {s.kind for s in spans} == {"agent", "model", "tool"}
    assert all(s.content is None for s in spans)


def test_server_policy_caps_client_policy(server, tmp_path, monkeypatch):
    bt = client(server.url, tmp_path, content="full")
    trace_id = emit(bt)
    bt.close(timeout=10)
    _, spans = stored(trace_id)
    assert all(s.content is None for s in spans)  # server default is metadata


def test_redacted_content_when_server_allows(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHTRACE_INGEST_CONTENT", "full")
    with LiveServer(free_port()) as srv:
        bt = client(srv.url, tmp_path, content="redacted")
        trace_id = emit(bt)
        bt.close(timeout=10)
    _, spans = stored(trace_id)
    model = next(s for s in spans if s.kind == "model")
    prompt = model.content["benchtrace.content.prompt"]
    assert "What is 2+2?" in prompt and "sk-abcdef" not in prompt and "[REDACTED:openai_key]" in prompt


def test_spans_survive_a_crash_and_are_delivered_later(tmp_path):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    script = textwrap.dedent(f"""
        import os, sys
        from benchtrace.sdk import Benchtrace
        bt = Benchtrace({url!r}, spool_dir={str(tmp_path / "spool")!r}, set_global=False, flush_interval=0.05)
        with bt.trace("crashy") as root:
            with bt.span("step", kind="tool"):
                pass
        print(format(root.get_span_context().trace_id, "032x"), flush=True)
        os._exit(1)  # crash before anything could be delivered (no server is running)
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    trace_id = out.stdout.strip()
    assert len(trace_id) == 32, out.stderr
    assert any((tmp_path / "spool").rglob("*.otlp"))

    with LiveServer(port):
        bt = client(url, tmp_path)
        health = bt.close(timeout=15)
    assert health.pending_batches == 0
    trace, spans = stored(trace_id)
    assert len(spans) == 2 and trace.state == "closed"


def test_retried_batch_is_not_stored_twice(server, tmp_path):
    # Spool against an unreachable endpoint so the batch stays on disk, then deliver it twice by hand.
    offline = client("http://127.0.0.1:9", tmp_path)
    trace_id = emit(offline)
    offline.processor.force_flush()
    batch = next((tmp_path / "spool").rglob("*.otlp"))
    data, batch_id = batch.read_bytes(), batch.name.split(".")[0]
    offline.close(timeout=0.5)

    def post():
        req = urllib.request.Request(
            server.url + "/v1/traces",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-protobuf", "x-benchtrace-batch-id": batch_id},
        )
        with urllib.request.urlopen(req) as resp:
            return resp.headers["x-benchtrace-duplicate"]

    assert post() == "false"
    assert post() == "true"
    _, spans = stored(trace_id)
    assert len(spans) == 3


def test_rejected_batch_is_set_aside_and_reported(server, tmp_path):
    bt = client(server.url, tmp_path)
    bt.spool.write(b"not a protobuf \xff\xfe", ".otlp")
    trace_id = emit(bt)
    health = bt.close(timeout=10)
    assert health.rejected_batches == 1 and "rejected" in health.last_error
    assert list((tmp_path / "spool").rglob("rejected/*.otlp"))
    assert stored(trace_id)[0].state == "closed"


def test_full_spool_refuses_spans_visibly(tmp_path):
    bt = client("http://127.0.0.1:9", tmp_path, max_spool_bytes=10)
    emit(bt)
    bt.processor.force_flush()
    health = bt.close(timeout=1)
    assert health.refused_spans > 0 and "full" in health.last_error


def test_otlp_json_with_openinference_kinds(server):
    body = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "app"}}]},
                "scopeSpans": [
                    {
                        "scope": {"name": "openinference"},
                        "spans": [
                            {
                                "traceId": "5b8efff798038103d269b633813fc60c",
                                "spanId": "eee19b7ec3c1b174",
                                "name": "chat",
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000001000000000",
                                "attributes": [
                                    {"key": "openinference.span.kind", "value": {"stringValue": "LLM"}},
                                    {"key": "llm.token_count.prompt", "value": {"intValue": "12"}},
                                    {"key": "input.value", "value": {"stringValue": "secret prompt"}},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    req = urllib.request.Request(
        server.url + "/v1/traces",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        assert json.load(resp)["benchtrace"]["accepted_spans"] == 1
    trace, [span] = stored("5b8efff798038103d269b633813fc60c")
    assert span.kind == "model" and span.attributes["gen_ai.usage.input_tokens"] == 12
    assert span.content is None and span.content_state == "withheld"
    assert trace.state == "open"
