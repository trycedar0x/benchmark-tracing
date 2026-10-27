"""Consumer skills stay portable and their machine-readable examples work offline."""

import copy
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
spec = importlib.util.spec_from_file_location("skill_evaluate", SKILLS / "everybench/scripts/evaluate.py")
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


def comparison():
    return {
        "compatibility": {"comparable": True, "blocking": [], "warnings": []},
        "errors": {"a_only": 0, "b_only": 0, "both": 0},
        "missing": {"only_a": 0, "only_b": 0},
        "n_scored_pairs": 20,
        "delta_ci95": [-0.01, 0.03],
        "rows": [],
    }


def test_gate_policy():
    result = comparison()
    assert evaluator.assess(result, 0, 2)[0] == "pass"
    result["delta_ci95"] = [-0.2, -0.03]
    assert evaluator.assess(result, 0.02, 2)[0] == "regression"
    assert evaluator.assess(result, 0.04, 2)[0] == "pass"
    assert evaluator.assess(result, 0, 21)[0] == "blocked"


@pytest.mark.parametrize(
    "key, value",
    [
        ("compatibility", {"comparable": False, "warnings": [], "blocking": ["variant mismatch"]}),
        ("compatibility", {"comparable": True, "warnings": ["partial overlap"], "blocking": []}),
        ("errors", {"a_only": 0, "b_only": 1, "both": 0}),
        ("missing", {"only_a": 1, "only_b": 0}),
        ("rows", [{"change": "missing"}]),
        ("delta_ci95", None),
    ],
)
def test_gate_blocks_incomplete_evidence(key, value):
    result = comparison() | {key: value}
    assert evaluator.assess(result, 0, 2)[0] == "blocked"


def test_timeout_report_preserves_ids(monkeypatch, capsys):
    def evaluate(client, body, max_drop, min_pairs, report):
        report["run_ids"] = ["run_a", "run_b"]
        raise TimeoutError()

    monkeypatch.setattr(evaluator, "evaluate", evaluate)
    assert evaluator.main(["--baseline", "run_a", "--candidate", "run_b"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "timeout"
    assert report["run_ids"] == ["run_a", "run_b"]


def test_polling_deadline_preserves_run_ids():
    class ExpiredClient:
        deadline = 0

        def api(self, method, path, body=None):
            assert method == "GET"
            return {"id": path.rsplit("/", 1)[1], "status": "running"}

    report = {}
    with pytest.raises(TimeoutError):
        evaluator.evaluate(ExpiredClient(), ["run_a", "run_b"], 0, 2, report)
    assert report["run_ids"] == ["run_a", "run_b"]


@pytest.mark.parametrize("status,outcomes", [("failed", {}), ("budget_exceeded", {}), ("succeeded", {"error": 1})])
def test_run_status_and_sample_errors_block_gate(status, outcomes):
    class FinishedClient:
        def api(self, method, path, body=None):
            assert method == "GET"
            assert path.startswith("/api/runs/")  # must not compare unusable results
            return {"id": path.rsplit("/", 1)[1], "status": status, "outcomes": outcomes}

    report = {}
    assert evaluator.evaluate(FinishedClient(), ["run_a", "run_b"], 0, 2, report) == 2
    assert report["decision"] == "blocked"


def test_skills_have_metadata_and_self_contained_links():
    for name in ("everybench", "everybench-sdk"):
        folder = SKILLS / name
        skill = (folder / "SKILL.md").read_text()
        metadata = yaml.safe_load(skill.split("---", 2)[1])
        assert metadata["name"] == name
        assert len(metadata["description"]) > 40
        for path in folder.rglob("*.md"):
            for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text()):
                if "://" in link or link.startswith("#"):
                    continue
                target = (path.parent / link.split("#")[0]).resolve()
                assert target.is_relative_to(folder.resolve()), (path, link)
                assert target.exists(), (path, link)


def test_agent_documentation_links():
    for path in (ROOT / "AGENTS.md", ROOT / "docs/agents.md", SKILLS / "README.md"):
        for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text()):
            if "://" not in link and not link.startswith("#"):
                assert (path.parent / link.split("#")[0]).exists(), (path, link)


def test_documented_api_routes_exist():
    from everybench.server import create_app

    with TestClient(create_app(workers=0)) as client:
        paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/health",
        "/api/catalog",
        "/api/evaluation-targets",
        "/api/runs",
        "/api/runs/{run_id}",
        "/api/runs/{run_id}/samples",
        "/api/compare",
        "/api/traces/{trace_id}",
        "/api/trace-diff",
        "/v1/traces",
    ):
        assert path in paths


@pytest.fixture
def live_server(monkeypatch):
    # Keep a bound socket through startup to avoid a free-port race.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    from everybench.server import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(workers=2), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        monkeypatch.setenv("EVERYBENCH_URL", f"http://127.0.0.1:{port}")
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()


def run_portable_script(tmp_path, skill, script, *args):
    destination = tmp_path / "installed" / skill
    shutil.copytree(SKILLS / skill, destination)
    # Run outside the repo: installed skills cannot rely on relative repo imports/files.
    result = subprocess.run(
        [sys.executable, str(destination / "scripts" / script), *args],
        cwd=tmp_path,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        timeout=90,
    )
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize("candidate,code,decision", [("mock/strong", 0, "pass"), ("mock/weak", 1, "regression")])
def test_portable_evaluation_script(live_server, tmp_path, candidate, code, decision):
    from historical import historical_run

    a, b = historical_run(limit=40), historical_run(model=candidate, limit=40)
    actual, report = run_portable_script(
        tmp_path, "everybench", "evaluate.py", "--baseline", a.id, "--candidate", b.id, "--timeout", "60"
    )
    assert actual == code, report
    assert report["schema"] == "everybench.agent-eval/v1"
    assert report["decision"] == decision
    assert len(report["run_ids"]) == 2
    assert report["comparison"]["n_scored_pairs"] == 40


def test_portable_sdk_script(live_server, tmp_path):
    code, report = run_portable_script(tmp_path, "everybench-sdk", "trace_smoke.py", "--timeout", "10")
    assert code == 0, report
    assert report["schema"] == "everybench.sdk-smoke/v1"
    assert report["decision"] == "delivered"
    assert report["answer"] == 391
    receipt = report["health"]["receipts"][report["trace_id"]]
    assert receipt["state"] == "closed"
    assert receipt["expected_spans"] == receipt["received_spans"] == 3


def test_public_evaluation_exports_and_portable_documented_example(tmp_path):
    from everybench.sdk import EveryBench, evaluate, evaluate_sync

    assert callable(evaluate) and callable(evaluate_sync)
    assert not hasattr(EveryBench, "evaluate")  # Module functions, not invented client methods.
    folder = tmp_path / "installed" / "everybench-sdk"
    shutil.copytree(SKILLS / "everybench-sdk", folder)
    reference = (folder / "references/evaluation.md").read_text()
    code = reference.split("```python\n", 1)[1].split("```", 1)[0]
    script = tmp_path / "evaluation_example.py"
    script.write_text(code)
    env = os.environ.copy()
    for prefix in ("EVERYBENCH", "EVERYEVAL", "BENCHTRACE"):
        env.pop(f"{prefix}_DATABASE_URL", None)
    env["EVERYBENCH_HOME"] = str(tmp_path / "evaluation-home")
    result = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "everybench.agent-evaluation.v1"
    assert report["status"] == "succeeded" and report["results"][0]["score"] == 1
    assert report["metrics"]["cost_usd"] is None
    assert (tmp_path / "evaluation-home/files/evaluations" / report["run_id"] / "report.json").is_file()


def test_evaluator_does_not_mutate_comparison():
    result = comparison()
    original = copy.deepcopy(result)
    evaluator.assess(result, 0, 2)
    assert result == original
