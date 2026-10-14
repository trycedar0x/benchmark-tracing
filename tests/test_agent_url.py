"""External research agent: EveryBench posts frozen cases to its URL and scores the reply."""

import importlib.util
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from everybench.catalog import get_benchmark
from everybench.compare import compare_runs
from everybench.db import SampleResult, session_scope
from everybench.execution import execute_run
from everybench.research_cases import grade
from everybench.server import create_app
from everybench.service import PlanError, create_runs


def _agent():
    path = Path(__file__).resolve().parents[1] / "examples" / "research_agent" / "agent.py"
    spec = importlib.util.spec_from_file_location("research_agent_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _serve(version: str) -> str:
    module = _agent()
    server = module.make_server("127.0.0.1", 0, version)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/answer"


def _run(url: str):
    [run] = create_runs(get_benchmark("research-cases"), [], agent_url=url)
    return execute_run(run.id)


def test_score_requires_the_verdict_and_both_checks():
    outcome, score, explanation = grade({"answer": "bounce", "checked": ["headline"]})
    assert (outcome, score) == ("incorrect", 0.0)
    assert "trend_20d" in explanation and "earnings_call" in explanation
    outcome, _, _ = grade({"answer": "get more bullish", "checked": ["trend_20d", "earnings_call"]})
    assert outcome == "incorrect"
    outcome, score, _ = grade({"answer": "bounce", "checked": ["trend_20d", "earnings_call"]})
    assert (outcome, score) == ("correct", 1.0)


def test_v1_fails_and_v2_passes_against_the_same_cases():
    v1, v2 = _run(_serve("v1")), _run(_serve("v2"))
    assert v1.status == v2.status == "succeeded"
    assert (v1.n_correct, v1.samples_done) == (0, 6)
    assert (v2.n_correct, v2.samples_done) == (6, 6)

    with session_scope() as session:
        first = session.scalars(
            select(SampleResult).where(SampleResult.run_id == v1.id, SampleResult.sample_id == "case-1")
        ).one()
        second = session.scalars(
            select(SampleResult).where(SampleResult.run_id == v2.id, SampleResult.sample_id == "case-1")
        ).one()
    assert "Frozen case, not live news." in first.input
    assert first.input.startswith("Case 1 of 6.")
    assert "NVDA rose about 3% yesterday on an AI-demand headline." in first.input
    assert first.answer == "get more bullish" and first.outcome == "incorrect"
    assert "trend_20d" in first.explanation
    assert second.answer == "bounce" and second.outcome == "correct"
    assert "trend_20d" in second.output and "earnings_call" in second.output

    comparison = compare_runs(v1.id, v2.id)
    assert comparison.compatibility.comparable
    assert comparison.mean_a == 0 and comparison.mean_b == 1
    assert comparison.discordant["improvements"] == 6


def test_api_accepts_an_agent_url_and_rejects_a_model_for_these_cases():
    url = _serve("v1")
    with TestClient(create_app(workers=1)) as client:
        missing = client.post("/api/runs", json={"benchmark": "research-cases", "models": ["mock/strong"]})
        assert missing.status_code == 400
        started = client.post("/api/runs", json={"benchmark": "research-cases", "agent_url": url})
        assert started.status_code == 201, started.text
        run_id = started.json()[0]["id"]
        assert started.json()[0]["model"] == url

        deadline = time.time() + 30
        while time.time() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(0.2)
        assert run["status"] == "succeeded"
        assert run["n_correct"] == 0 and run["samples_done"] == 6


def test_research_cases_need_an_agent_url():
    try:
        create_runs(get_benchmark("research-cases"), ["mock/strong"])
    except PlanError as ex:
        assert "agent URL" in str(ex)
    else:
        raise AssertionError("expected PlanError")
