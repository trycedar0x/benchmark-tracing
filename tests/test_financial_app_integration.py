"""Actual sibling app HTTP -> actual receiver/seal -> finance scorer, entirely offline.

Requires the sibling's credential-free npm ci/build. Standalone EveryBench CI may
skip this cross-repository gate; the integration checkout must run it explicitly.
"""

import asyncio
import hashlib
import json
import selectors
import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import uvicorn
from opentelemetry import trace
from sqlalchemy import select

from everybench.artifacts import sample_artifact_path
from everybench.db import Span, session_scope
from everybench.evaluation import HttpAgent, HTTPTraceSource, evaluate, report_path
from everybench.finance import judging
from everybench.finance.datasets import DATASETS
from everybench.server import create_app

APP = Path(__file__).resolve().parents[2] / "fin-research-agent/financial-research-app"
REFERENCE = {
    "id": "offline-original-case-42",
    "query": "Look up AAPL in the SEC company directory.",
    "reference_answer": "EVALUATOR_ONLY_REFERENCE",
    "rubric": [{"text": "EVALUATOR_ONLY_RUBRIC", "points": 2}],
    "do_not_train": True,
}
BODY_KEYS = {"gen_ai.tool.call.arguments", "gen_ai.tool.call.result"}
MODEL_SECRET = "opaqueSyntheticModelCredential91735"
CALL_SECRET = "opaqueSyntheticCallCredential24860"
SCENARIOS = {
    "oversized": {"answerChunks": ["A" * 10_000] * 4},
    "secret-attributes": {
        "modelId": f"offline/{MODEL_SECRET}",
        "toolCallId": CALL_SECRET,
        "tracingContent": "metadata",
        "secrets": [MODEL_SECRET, CALL_SECRET],
    },
}


@contextmanager
def receiver(policy, monkeypatch):
    monkeypatch.setenv("EVERYBENCH_INGEST_CONTENT", policy)
    app = create_app()
    wire = []

    @app.middleware("http")
    async def capture(request, call_next):
        if request.url.path == "/v1/traces":
            wire.append(json.loads(await request.body()))
        return await call_next(request)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, "Receiver failed to start"
            time.sleep(0.01)
        yield url, wire
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive(), "Receiver failed to stop"


@contextmanager
def financial_app(receiver_url, mode, scenario=None):
    if shutil.which("node") is None or not (APP / "dist/tracing.js").exists():
        pytest.skip("Cross-repository gate requires Node and sibling npm ci/build")
    fixture = Path(__file__).parent / "fixtures/financial_app.mjs"
    # Do not forward credentials or invoke the standalone env-file loader.
    process = subprocess.Popen(
        [shutil.which("node"), str(fixture), str(APP), receiver_url, mode, json.dumps(scenario or {})],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"NODE_ENV": "test"},
    )
    stats = {}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(10), "Injected app did not start"
        line = process.stdout.readline()
        assert line, "Injected app exited before readiness"
        yield json.loads(line)["url"], stats
    finally:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise AssertionError("Injected app did not shut down") from None
        assert process.returncode == 0, stderr
        stats.update(json.loads(stdout.strip()))


@pytest.mark.parametrize(
    ("policy", "mode", "status", "trace_state"),
    [
        ("full", "success", "scored", "complete"),
        ("metadata", "success", "trace_missing", "withheld"),
        ("full", "missing", "trace_missing", "missing"),
        ("full", "partial", "agent_error", None),
        ("full", "oversized", "trace_missing", "withheld"),
        ("metadata", "secret-attributes", "trace_missing", "withheld"),
    ],
)
def test_actual_financial_app_receiver_finance_report(
    monkeypatch, tmp_path, policy, mode, status, trace_state, everybench_home
):
    body = json.dumps(REFERENCE).encode()
    monkeypatch.setitem(
        DATASETS,
        "bigfinance-public-50",
        replace(DATASETS["bigfinance-public-50"], sha256=hashlib.sha256(body).hexdigest(), count=1),
    )
    data_path = tmp_path / "offline-public-50.jsonl"
    data_path.write_bytes(body)
    judged = []

    async def fake_judge(model, role, instruction, evidence):
        assert model == "mock/perfect" and role == "finance_rubric_judge_1"
        assert not trace.get_current_span().get_span_context().is_valid
        assert evidence["reference_answer"] == "EVALUATOR_ONLY_REFERENCE"
        assert "Apple Offline Fixture" in evidence["trace"]
        assert "actual-fixture-call-1" in evidence["trace"]
        assert "gen_ai.tool.call.arguments" in evidence["trace"]
        assert "gen_ai.tool.call.result" in evidence["trace"]
        assert "PRIVATE_PROVIDER_FAILURE" not in evidence["trace"]
        # Inspect the MODEL event itself, not the later executed TOOL span.
        first_model = evidence["trace"].split("\ntool financial.tool.", 1)[0]
        assert first_model.startswith("model financial.model ")
        output = next(line for line in first_model.splitlines() if line.startswith("gen_ai.output.messages: "))
        assert json.loads(output.split(": ", 1)[1]) == [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "actual-fixture-call-1", "name": "lookupCompany", "arguments": {"query": "AAPL"}}
                ],
            }
        ]
        judged.append(evidence)
        return {
            "final_answer_correct": True,
            "rubric": [{"index": 1, "satisfied": True, "explanation": "Captured SEC fixture."}],
        }

    monkeypatch.setattr(judging, "ask_json", fake_judge)
    with receiver(policy, monkeypatch) as (receiver_url, wire):
        with financial_app(receiver_url, mode, SCENARIOS.get(mode)) as (app_url, stats):
            report = asyncio.run(
                evaluate(
                    "bigfinance-public-50",
                    HttpAgent(
                        app_url + "/api/chat",
                        api_key="synthetic-integration-agent-bearer-token-32-bytes-minimum",
                        name="actual-financial-app",
                        version="offline-fixture",
                    ),
                    trace_source=HTTPTraceSource(receiver_url),
                    allow_paid=True,
                    data_path=data_path,
                    judge_models=["mock/perfect"],
                    content_policy="full",
                    trace_timeout=0.6,
                    timeout=5,
                )
            )
            result = report.results[0]
            assert result.status == status, report.model_dump_json()
            assert result.trace_state == trace_state
            assert result.score == (1 if status == "scored" else None)
            assert len(judged) == (1 if status == "scored" else 0)
            assert report.metrics["cost_usd"] is None
            saved = json.loads(report_path(report.run_id).read_text())
            assert saved == report.model_dump(mode="json")
            with httpx.Client(base_url=receiver_url, trust_env=False) as client:
                run = client.get(f"/api/runs/{report.run_id}").json()
                assert run["target_kind"] == "external_agent" and run["model"] is None
                assert run["target"]["name"] == "actual-financial-app"
                samples = client.get(f"/api/runs/{report.run_id}/samples").json()["items"]
                assert len(samples) == 1 and samples[0]["sample_id"] == REFERENCE["id"]
                evidence_api = client.get(
                    f"/api/runs/{report.run_id}/sample-evidence", params={"sample_id": REFERENCE["id"]}
                )
                assert evidence_api.status_code == 200
                assert sample_artifact_path(report.run_id, REFERENCE["id"], 1).exists()
                if mode != "missing":
                    deadline = time.monotonic() + 5
                    while True:
                        payload = client.get(f"/api/traces/{result.trace_id}").json()
                        if payload.get("trace", {}).get("state") == "closed":
                            break
                        assert time.monotonic() < deadline, payload
                        time.sleep(0.01)
                    spans = payload["spans"]
                    assert len(spans) == 4 and {s["kind"] for s in spans} == {"agent", "model", "tool"}
                    assert payload["trace"]["attributes"]["expected_spans"] == 4
                    tool = next(s for s in spans if s["kind"] == "tool")
                    assert tool["attributes"]["gen_ai.tool.call.id"] == (
                        "[REDACTED]" if mode == "secret-attributes" else "actual-fixture-call-1"
                    )
                    assert BODY_KEYS.isdisjoint(tool["attributes"])
                    assert all(
                        "tool.call.arguments" not in s["attributes"] and "tool.call.result" not in s["attributes"]
                        for s in spans
                    )
                    if policy == "metadata":
                        expected_content_state = "missing" if mode == "secret-attributes" else "withheld"
                        assert all(s["content"] is None and s["content_state"] == expected_content_state for s in spans)
                        if mode == "secret-attributes":
                            assert all(s["attributes"]["everybench.evidence.withheld"] is True for s in spans)
                        assert "Apple Offline Fixture" not in json.dumps(spans)
                        with session_scope() as session:
                            persisted = session.scalars(select(Span).where(Span.trace_id == result.trace_id)).all()
                            assert all(
                                s.content is None and "Apple Offline Fixture" not in json.dumps(s.attributes)
                                for s in persisted
                            )
                    else:
                        assert BODY_KEYS <= tool["content"].keys()
                        assert "Apple Offline Fixture" in tool["content"]["gen_ai.tool.call.result"]
                        if mode == "oversized":
                            assert result.answer == "A" * 40_000  # Successful NDJSON done, not an invocation failure.
                            assert all(s["status"] == "ok" for s in spans)
                            root = next(s for s in spans if s["kind"] == "agent")
                            last_model = next(
                                s
                                for s in spans
                                if s["kind"] == "model" and s["attributes"].get("everybench.evidence.withheld")
                            )
                            for span, key in ((root, "output.value"), (last_model, "gen_ai.output.messages")):
                                assert span["attributes"]["everybench.evidence.withheld"] is True
                                assert span["attributes"]["everybench.evidence.withheld_keys"] == [key]
                                assert key not in (span["content"] or {})
                            assert "gen_ai.input.messages" in last_model["content"]
                            assert "A" * 100 not in json.dumps(spans)
                        if mode == "partial":
                            assert sum(s["status"] == "error" for s in spans) == 2
                            assert tool["status"] == "ok"
                            assert result.answer is None
                    wire_spans = [
                        s
                        for r in wire
                        for rs in r["resourceSpans"]
                        for scope in rs["scopeSpans"]
                        for s in scope["spans"]
                    ]
                    assert len(wire_spans) == 4
                    wire_tool = next(s for s in wire_spans if s["name"] == "financial.tool.lookupCompany")
                    if mode == "secret-attributes":
                        assert BODY_KEYS.isdisjoint(a["key"] for a in wire_tool["attributes"])
                        assert {a["key"]: a["value"] for a in wire_tool["attributes"]}["gen_ai.tool.call.id"] == {
                            "stringValue": "[REDACTED]"
                        }
                        wire_models = [s for s in wire_spans if s["name"] == "financial.model"]
                        assert len(wire_models) == 2
                        assert all(
                            {a["key"]: a["value"] for a in s["attributes"]}["gen_ai.request.model"]
                            == {"stringValue": "offline/[REDACTED]"}
                            for s in wire_models
                        )
                        for secret in (MODEL_SECRET, CALL_SECRET):
                            assert secret not in json.dumps(wire)
                            assert secret not in json.dumps(spans)
                        with session_scope() as session:
                            persisted = session.scalars(select(Span).where(Span.trace_id == result.trace_id)).all()
                            assert len(persisted) == 4
                            assert all(
                                secret not in json.dumps(s.attributes)
                                for s in persisted
                                for secret in (MODEL_SECRET, CALL_SECRET)
                            )
                            assert all(s.content is None for s in persisted)
                        assert all(
                            s["attributes"]["gen_ai.request.model"] == "offline/[REDACTED]"
                            for s in spans
                            if s["kind"] == "model"
                        )
                    else:
                        assert BODY_KEYS <= {a["key"] for a in wire_tool["attributes"]}, (
                            "Producer must send FULL content even to metadata receiver"
                        )
                    assert "EVALUATOR_ONLY_" not in json.dumps(wire)
                    assert "PRIVATE_PROVIDER_FAILURE" not in json.dumps(wire)
        assert stats["requests"] == [{"message": REFERENCE["query"]}]
        assert stats["modelCalls"] == 2 and len(stats["secRequests"]) == 1
    (tmp_path / "integration-evidence.json").write_text(
        json.dumps(
            {
                "run_id": report.run_id,
                "report": str(report_path(report.run_id)),
                "sample_evidence": str(sample_artifact_path(report.run_id, REFERENCE["id"], 1)),
                "home": str(everybench_home),
                "status": status,
                "receiver_policy": policy,
                "mode": mode,
            },
            indent=2,
        )
    )
