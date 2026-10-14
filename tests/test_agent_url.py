"""The example research assistant runs a real tool loop, and the try page describes the result."""

import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage

from everybench.catalog import get_benchmark
from everybench.research_cases import grade
from everybench.server import create_app
from everybench.service import PlanError, create_runs


def _agent():
    path = Path(__file__).resolve().parents[1] / "examples" / "research_agent" / "agent.py"
    spec = importlib.util.spec_from_file_location("research_agent_example", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_score_requires_the_verdict_and_both_checks():
    outcome, score, explanation = grade({"answer": "bounce", "checked": ["headline"]})
    assert (outcome, score) == ("incorrect", 0.0)
    assert "trend_20d" in explanation and "earnings_call" in explanation
    outcome, _, _ = grade({"answer": "get more bullish", "checked": ["trend_20d", "earnings_call"]})
    assert outcome == "incorrect"
    outcome, score, _ = grade({"answer": "bounce", "checked": ["trend_20d", "earnings_call"]})
    assert (outcome, score) == ("correct", 1.0)


def test_the_assistant_runs_the_headline_tool():
    result = _agent().answer("NVDA rose about 3% yesterday on an AI-demand headline. Should I be more bullish?")
    assert result["tools"] == ["read_headline"]
    assert result["answer"] == "get more bullish"
    notes = [m.content for m in result["messages"] if isinstance(m, ToolMessage)]
    assert notes == ["NVDA rose about 3% on an AI-demand headline"]


def test_try_page_describes_each_miss_in_plain_language():
    with TestClient(create_app()) as client:
        response = client.post("/api/try-agent")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"] == "It got 0 of 6 right."
    assert len(body["cases"]) == 6
    first = body["cases"][0]
    assert first["question"] == "NVDA rose about 3% yesterday on an AI-demand headline. Should I be more bullish?"
    assert first["line"] == (
        'It answered "get more bullish." It did not check the last 20 trading days, which are down, '
        "or the earnings call, which was more cautious than expected."
    )
    text = json.dumps(body).lower()
    for word in ("langgraph", "benchmark", "http://", "mock/"):
        assert word not in text


def test_research_cases_need_an_agent_url():
    try:
        create_runs(get_benchmark("research-cases"), ["mock/strong"])
    except PlanError as ex:
        assert "agent URL" in str(ex)
    else:
        raise AssertionError("expected PlanError")
