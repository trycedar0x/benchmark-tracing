"""Offline API -> real child -> shared engine -> same run/report coverage."""

import json
import subprocess
import threading
import time
from dataclasses import asdict
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_evaluation_finance_cli import pinned_data

from everybench.auth import set_secret
from everybench.db import Job, Run, now, session_scope
from everybench.evaluation.engine import report_path
from everybench.execution import execute_run, request_cancel
from everybench.finance.datasets import DATASETS
from everybench.jobs import Worker, claim, finish, process, recover_stale
from everybench.server import create_app

REQUEST = {
    "benchmark": "findeepindicator",
    "target_id": "agent",
    "judge_models": ["mock/perfect"],
    "extractor_model": "mock/perfect",
    "allow_paid": True,
    "limit": 1,
    "content_policy": "full",
}


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setenv("EVERYBENCH_SECRET_KEY", "test-master")
    monkeypatch.delenv("EVERYEVAL_AUTH", raising=False)
    monkeypatch.delenv("EVERYEVAL_DATABASE_URL", raising=False)
    data = pinned_data(monkeypatch, tmp_path, "findeepindicator")
    calls = []
    trace_calls = []
    started, release = threading.Event(), threading.Event()
    release.set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(
                {
                    "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                    "headers": dict(self.headers),
                }
            )
            started.set()
            release.wait(10)
            body = (
                json.dumps({"type": "conversation", "conversationId": "a" * 43})
                + "\n"
                + json.dumps({"type": "delta", "text": "12.5"})
                + "\n"
                + '{"type":"done"}\n'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def do_GET(self):
            trace_calls.append({"path": self.path, "headers": dict(self.headers)})
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = tmp_path / "targets.json"
    profile = {
        "id": "agent",
        "name": "Owned agent",
        "version": "v1",
        "workspace_id": None,
        "url": f"http://127.0.0.1:{server.server_port}/private-capability",
        "token_secret": "AGENT_TOKEN",
        "data_path": str(data),
    }
    path.write_text(json.dumps([profile]))
    monkeypatch.setenv("EVERYBENCH_TARGETS_FILE", str(path))
    set_secret(None, "AGENT_TOKEN", "local-agent-secret")
    real_popen = subprocess.Popen
    spec = asdict(DATASETS["findeepindicator"])
    # Test-only bootstrap supplies pinned synthetic data and deterministic JSON judging IN THE CHILD.
    # No production monkeypatch hooks or toy public benchmark are introduced.
    bootstrap = f"""
import asyncio, sys
from everybench.finance.datasets import DATASETS, DatasetSpec
from everybench.finance import judging
from everybench.evaluation.worker import run_evaluation
DATASETS["findeepindicator"] = DatasetSpec(**{spec!r})
async def fake_judge(model, role, instruction, evidence):
    assert model == "mock/perfect"
    assert role == "finance_extractor"
    return {{"answer": "12.5", "formula": None, "data": None, "data_indicator": None}}
judging.ask_json = fake_judge
asyncio.run(run_evaluation(sys.argv[1]))
"""

    def spawn(command, **kwargs):
        assert command[1:3] == ["-m", "everybench.evaluation.worker"]
        return real_popen([command[0], "-c", bootstrap, command[-1]], **kwargs)

    monkeypatch.setattr("everybench.execution.subprocess.Popen", spawn)
    with TestClient(create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        yield {
            "client": client,
            "calls": calls,
            "trace_calls": trace_calls,
            "path": path,
            "profile": profile,
            "started": started,
            "release": release,
            "bootstrap": bootstrap,
            "spawn": real_popen,
            "data": data,
        }
    release.set()
    server.shutdown()
    thread.join(5)
    server.server_close()


def queued(backend, **updates):
    response = backend["client"].post("/api/runs", json=REQUEST | updates)
    assert response.status_code == 201, response.text
    return response.json()


def run_job():
    job = claim("offline-test")
    assert job is not None
    try:
        process(job)
    except RuntimeError:
        finish(job.id, "Evaluation failed.")
    else:
        finish(job.id)
    return job


def test_api_job_shared_engine_same_identity(backend):
    run = queued(backend)
    assert run["status"] == "queued"
    job = run_job()
    final = backend["client"].get(f"/api/runs/{run['id']}").json()
    assert final["status"] == "succeeded", final
    assert final["samples_done"] == 1 and final["score"] == 1
    assert final["id"] == job.target_id
    assert json.loads(report_path(run["id"]).read_text())["run_id"] == run["id"]
    assert backend["calls"][0]["body"] == {"message": "Compute the ratio."}
    assert backend["calls"][0]["headers"]["Authorization"] == "Bearer local-agent-secret"
    assert backend["calls"][0]["headers"]["x-everybench-run-id"] == run["id"]
    assert "local-agent-secret" not in json.dumps(final)
    assert "private-capability" not in json.dumps(final)
    assert "AGENT_TOKEN" not in json.dumps(final)
    assert "evaluation_submission" in final["manifest"]
    with session_scope() as session:
        assert session.scalar(select(func.count(Run.id))) == 1
        assert session.get(Job, job.id).status == "done"
    assert execute_run(run["id"]).status == "succeeded"
    assert len(backend["calls"]) == 1


def test_queued_cancellation_never_invokes(backend):
    run = queued(backend)
    assert backend["client"].post(f"/api/runs/{run['id']}/cancel").json()["status"] == "cancelled"
    run_job()
    assert backend["calls"] == []


def test_running_cancellation_persists_partial_report(backend):
    backend["release"].clear()
    run = queued(backend)
    errors = []

    def work():
        try:
            run_job()
        except Exception as ex:
            errors.append(ex)

    thread = threading.Thread(target=work)
    thread.start()
    assert backend["started"].wait(15)
    assert request_cancel(run["id"]).status == "cancelling"
    thread.join(15)
    backend["release"].set()
    assert not thread.is_alive() and not errors
    final = backend["client"].get(f"/api/runs/{run['id']}").json()
    assert final["status"] == "cancelled" and final["samples_done"] == 1
    report = json.loads(report_path(run["id"]).read_text())
    assert report["status"] == "cancelled" and report["results"][0]["status"] == "cancelled"


@pytest.mark.parametrize("failure", ["profile", "missing-secret", "bad-data", "spawn", "crash"])
def test_fail_closed_without_invocation(backend, monkeypatch, failure):
    run = queued(backend)
    if failure == "profile":
        backend["path"].write_text(json.dumps([backend["profile"] | {"version": "changed"}]))
    elif failure == "missing-secret":
        from everybench.auth import delete_secret

        delete_secret(None, "AGENT_TOKEN")
    elif failure == "bad-data":
        backend["data"].write_bytes(b"changed-data")
    elif failure == "spawn":

        def broken(*args, **kwargs):
            raise RuntimeError("PRIVATE PROVIDER SECRET")

        monkeypatch.setattr("everybench.execution.subprocess.Popen", broken)
    else:

        def crash(command, **kwargs):
            return backend["spawn"]([command[0], "-c", 'raise RuntimeError("PRIVATE PROVIDER SECRET")'], **kwargs)

        monkeypatch.setattr("everybench.execution.subprocess.Popen", crash)
    job = run_job()
    final = backend["client"].get(f"/api/runs/{run['id']}").json()
    assert final["status"] == "failed" and final["error"]
    assert "PRIVATE" not in json.dumps(final)
    assert backend["calls"] == []
    with session_scope() as session:
        assert session.get(Job, job.id).status == "failed"
    assert execute_run(run["id"]).status == "failed"
    assert backend["calls"] == []


def test_stale_paid_work_is_failed_not_replayed(backend):
    run = queued(backend)
    job = claim("lost-worker")
    with session_scope() as session:
        session.get(Job, job.id).heartbeat_at = now() - timedelta(minutes=5)
        session.get(Run, run["id"]).status = "running"
    assert recover_stale() == 1
    assert claim("replacement") is None
    assert execute_run(run["id"]).status == "failed"
    assert not backend["calls"]


def test_worker_records_sanitized_unexpected_failure(backend, monkeypatch):
    run = queued(backend)

    def broken(job):
        raise RuntimeError("PRIVATE PROVIDER SECRET")

    monkeypatch.setattr("everybench.jobs.process", broken)
    worker = Worker(concurrency=1, poll_seconds=0.01)
    worker.start()
    try:
        for _ in range(100):
            with session_scope() as session:
                job = session.scalar(select(Job))
            if job.status == "failed":
                break
            time.sleep(0.02)
        assert job.status == "failed" and "PRIVATE" not in job.error
        assert backend["client"].get(f"/api/runs/{run['id']}").json()["status"] == "failed"
        assert not backend["calls"]
    finally:
        worker.shutdown()


def test_unknown_sample_id_is_preflight_failure_without_agent(backend):
    run = queued(backend, sample_ids=["unknown"])
    run_job()
    final = backend["client"].get(f"/api/runs/{run['id']}").json()
    assert final["status"] == "failed" and "preflight" in final["error"]
    assert not backend["calls"]


@pytest.mark.parametrize("mode", ["preflight", "grading", "export"])
def test_failure_phases_are_distinct_and_sanitized(backend, monkeypatch, mode):
    bootstrap = backend["bootstrap"]
    insertion = {
        "preflight": """
from everybench.evaluation.benchmarks import FinanceBenchmark
async def broken(self):
    raise RuntimeError("PRIVATE PROVIDER SECRET")
FinanceBenchmark.validate = broken
""",
        "grading": """
async def broken(*args, **kwargs):
    raise RuntimeError("PRIVATE PROVIDER SECRET")
judging.ask_json = broken
""",
        "export": """
import everybench.evaluation.engine as engine
def broken(*args, **kwargs):
    raise RuntimeError("PRIVATE PROVIDER SECRET")
engine._write_report = broken
""",
    }[mode]
    bootstrap = bootstrap.replace(
        "asyncio.run(run_evaluation(sys.argv[1]))", insertion + "\nasyncio.run(run_evaluation(sys.argv[1]))"
    )

    def spawn(command, **kwargs):
        return backend["spawn"]([command[0], "-c", bootstrap, command[-1]], **kwargs)

    monkeypatch.setattr("everybench.execution.subprocess.Popen", spawn)
    run = queued(backend)
    run_job()
    final = backend["client"].get(f"/api/runs/{run['id']}").json()
    assert final["status"] == "failed" and "PRIVATE" not in json.dumps(final)
    if mode == "preflight":
        assert "preflight" in final["error"] and not backend["calls"]
    elif mode == "grading":
        assert final["samples_done"] == final["n_error"] == 1
        result = json.loads(report_path(run["id"]).read_text())["results"][0]
        assert result["status"] == "grading_error" and result["score"] is None
        assert len(backend["calls"]) == 1
    else:
        assert "persistence" in final["error"] and len(backend["calls"]) == 1


def test_queue_claim_is_exclusive(backend):
    run = queued(backend)
    jobs = []
    threads = [threading.Thread(target=lambda: jobs.append(claim("racing-worker"))) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert len([job for job in jobs if job is not None]) == 1
    assert jobs[[job is not None for job in jobs].index(True)].target_id == run["id"]


def test_run_and_job_rollback_together(backend):
    from sqlalchemy import event

    def reject_job(*args):
        raise RuntimeError("simulated queue storage failure")

    event.listen(Job, "before_insert", reject_job)
    try:
        with pytest.raises(RuntimeError, match="queue storage failure"):
            backend["client"].post("/api/runs", json=REQUEST)
    finally:
        event.remove(Job, "before_insert", reject_job)
    with session_scope() as session:
        assert session.scalar(select(func.count(Run.id))) == session.scalar(select(func.count(Job.id))) == 0
    assert not backend["calls"]


def test_stale_failure_cannot_be_overwritten_by_late_job_completion(backend):
    run = queued(backend)
    job = claim("lost-worker")
    with session_scope() as session:
        stored = session.get(Job, job.id)
        stored.heartbeat_at = None
        stored.created_at = now() - timedelta(minutes=5)
    assert recover_stale() == 1
    finish(job.id)
    with session_scope() as session:
        assert session.get(Job, job.id).status == "failed"
        assert session.get(Run, run["id"]).status == "failed"
    assert not backend["calls"]
