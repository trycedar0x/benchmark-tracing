"""Real Inspect execution with deterministic local models; all judges/tools are offline."""

import json

import pytest
from fastapi.testclient import TestClient
from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.model import get_model
from inspect_ai.scorer import Score, accuracy, scorer
from inspect_ai.solver import generate, system_message, use_tools
from typer.testing import CliRunner

from everybench.artifacts import read_sample_artifact
from everybench.bundled import calculator
from everybench.catalog import BenchmarkEntry
from everybench.child import _reconcile
from everybench.cli import app
from everybench.compare import check_compatibility
from everybench.db import Run, session_scope
from everybench.finance import judging, tasks
from everybench.ingest import store_sample
from everybench.inspect_convert import convert_sample
from everybench.server import create_app
from everybench.service import PlanError, billed_models, create_quote_record, create_runs

BIG = {
    "id": "case-1",
    "query": "What is 2 + 2? Use the calculator, then reply with the number.",
    "reference_answer": "4",
    "rubric": [{"text": "Use the correct inputs.", "points": 1}],
}
INDICATOR = {
    "id": "case-1",
    "question": "What is 2 + 2? Reply with only the number.",
    "answer": "4",
    "formula": "a+b",
    "data": "year,a,b\n2024,2,2\n",
    "data_indicator": "",
}


def finance_entry(**updates):
    values = dict(
        id="finance-dev",
        version=1,
        family="Finance development",
        variant="offline test fixture",
        task="everybench/finance_big_public50",
        grader="rubric",
        internal=True,
        task_args={"judge_models": ["mock/perfect"], "solver_mode": "answer_only"},
    )
    return BenchmarkEntry(**(values | updates))


@pytest.mark.parametrize("which", ["indicator", "big"])
def test_inspect_task_to_normalized_results_and_full_evidence(monkeypatch, tmp_path, which):
    monkeypatch.setattr(tasks, "load_dataset", lambda name: [INDICATOR if which == "indicator" else BIG])

    async def offline_judge(model, role, instruction, evidence):
        assert model == "mock/perfect"
        if role == "finance_extractor":
            return {"answer": "4", "formula": "a+b", "data": "year,a,b\n2024,2,2\n", "data_indicator": None}
        if role == "finance_formula_judge":
            return {"score": 2, "reason": "Correct sum."}
        assert "calculator" in evidence["trace"] and "tool: 4" in evidence["trace"]
        return {
            "final_answer_correct": True,
            "rubric": [{"index": 1, "satisfied": True, "explanation": "Actual calculator result is 4."}],
        }

    monkeypatch.setattr(judging, "ask_json", offline_judge)
    if which == "indicator":
        task = tasks.finance_indicator(judge_model="mock/perfect", solver_mode="answer_only")
        entry = finance_entry(task="everybench/finance_indicator", task_args={"judge_model": "mock/perfect"})
    else:
        task = tasks.finance_big_public50(judge_models=["mock/perfect"], solver_mode="answer_only")
        task.solver = [system_message("Use the calculator."), use_tools(calculator()), generate()]
        entry = finance_entry()
    [log] = inspect_eval(task, model="mock/perfect", log_dir=str(tmp_path / "logs"), display="none")
    assert log.status == "success", log.error
    assert log.samples[0].scores
    [run] = create_runs(entry, ["mock/perfect"])
    converted = convert_sample(log.samples[0], run.id)
    assert converted["result"]["score"] == 1
    assert converted["result"]["outcome"] == "correct"
    with session_scope() as session:
        store_sample(session, session.get(Run, run.id), converted)
        assert session.get(Run, run.id).cost_usd is None  # Never price all judges as the primary model.
    monkeypatch.setattr("everybench.child.get_benchmark", lambda ref: entry)
    _reconcile(run.id, log)
    with session_scope() as session:
        finished = session.get(Run, run.id)
        component = "answer" if which == "indicator" else "rubric"
        assert finished.metrics["finance"]["components"][component]["mean"] == 1
        assert finished.manifest["finance_evaluation"]["dataset"]["sha256"]
        assert finished.cost_usd is None
    evidence = read_sample_artifact(run.id, "case-1", 1)["content"]
    assert evidence["output"]
    metadata = next(iter(evidence["scores"].values()))["metadata"]
    assert metadata["protocol"].startswith("everybench.")
    if which == "big":
        assert metadata["scores"]["rubric"] == 1
        assert any(m["role"] == "tool" for m in evidence["messages"])
        assert json.loads(converted["result"]["score_raw"])["spec"]["key"] == "rubric"


def test_judge_role_does_not_look_like_evaluated_model_revision_drift(tmp_path):
    @scorer(metrics=[accuracy()])
    def auxiliary_judge():
        async def score(state, target):
            await get_model("mock/weak", role="finance_test_judge").generate("What is 1 + 1?")
            return Score(value=1)

        return score

    task = Task(dataset=[Sample(id="x", input="What is 2 + 2?", target="4")], scorer=auxiliary_judge())
    [log] = inspect_eval(task, model="mock/perfect", log_dir=str(tmp_path / "roles"), display="none")
    assert log.status == "success", log.error
    events = [ev for ev in log.samples[0].events if ev.event == "model"]
    assert any(ev.role == "finance_test_judge" for ev in events)
    converted = convert_sample(log.samples[0], "run")
    assert len(converted["resolved_models"]) == 1
    assert any(s["attributes"].get("model.role") == "finance_test_judge" for s in converted["spans"])


def test_evaluator_configuration_and_unmetered_costs_fail_closed():
    with pytest.raises(PlanError, match="explicitly"):
        create_runs(finance_entry(task_args={}), ["mock/perfect"])
    with pytest.raises(PlanError, match="all-inclusive budget"):
        create_runs(finance_entry(), ["mock/perfect"], budget_usd=1)
    with pytest.raises(PlanError, match="quotes are not supported"):
        create_quote_record(finance_entry(), ["mock/perfect"], limit=1, sample_size=1)
    configured = finance_entry(task_args={"judge_models": ["openai/explicit-judge"]})
    assert billed_models(configured, ["mock/perfect"]) == ["mock/perfect", "openai/explicit-judge"]


def test_paid_evaluator_cannot_bypass_cli_or_api_consent_with_mock_model(monkeypatch):
    entry = finance_entry(task_args={"judge_models": ["openai/explicit-judge"]})
    monkeypatch.setattr("everybench.cli.get_benchmark", lambda ref: entry)
    result = CliRunner().invoke(app, ["run", "finance-dev", "-m", "mock/perfect", "--limit", "1"])
    assert result.exit_code == 1
    assert "openai/explicit-judge" in result.output and "--yes" in result.output
    monkeypatch.setenv("EVERYBENCH_REQUIRE_QUOTE", "1")
    monkeypatch.setattr("everybench.server.get_benchmark", lambda ref: entry)
    with TestClient(create_app(workers=0)) as client:
        response = client.post("/api/runs", json={"benchmark": "finance-dev", "models": ["mock/perfect"]})
        assert response.status_code == 400
        assert "openai/explicit-judge" in response.json()["detail"]
        assert client.get("/api/runs").json() == []


def test_changed_finance_snapshot_blocks_comparison():
    a, b = create_runs(finance_entry(), ["mock/perfect", "mock/strong"])
    a.manifest = {**a.manifest, "finance_evaluation": {"dataset": {"revision": "one"}}}
    b.manifest = {**b.manifest, "finance_evaluation": {"dataset": {"revision": "two"}}}
    keys = {("sample", 1)}
    result = check_compatibility(a, b, keys, keys)
    assert not result.comparable
    assert any("Finance dataset" in reason for reason in result.blocking)
    b.manifest["finance_evaluation"] = a.manifest["finance_evaluation"]
    assert check_compatibility(a, b, keys, keys).comparable


def test_research_mode_requires_explicit_tools_before_model_calls(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        tasks.finance_indicator(judge_model="mock/perfect")
