"""Grading failures stay diagnosable and are never stored as a zero score."""

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
from sqlalchemy import select
from test_evaluation_engine import complete_evidence

from everybench.artifacts import grading_diagnostic_path, read_grading_diagnostic, read_sample_artifact
from everybench.db import Run, SampleResult, Span, session_scope
from everybench.evaluation import CallbackTraceSource, evaluate_sync
from everybench.finance import judging
from everybench.finance.big_finance import PROTOCOL
from everybench.finance.datasets import DATASETS

SAVED_ANSWER = "completed external answer"
SAMPLE_ID = "bf-0e2b33b21b"
QUERY = "Research earnings."
REFERENCE = "protected reference"
CRITERION = "protected criterion"
SECRET = "sk-abcdefghijklmnopqrstuvwx"
TRACE_BODY = "Actual captured model body"
FORBIDDEN = (QUERY, REFERENCE, CRITERION, SAVED_ANSWER, TRACE_BODY, "financial-research")


class _Choice:
    def __init__(self, stop_reason: str) -> None:
        self.stop_reason = stop_reason


class _Result:
    def __init__(self, completion: str = "", error: str | None = None, stop_reason: str = "stop") -> None:
        self.completion = completion
        self.error = error
        self.choices = [_Choice(stop_reason)]


class _Model:
    def __init__(self, script: list[_Result]) -> None:
        self.script = list(script)
        self.calls: list[tuple[list, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def generate(self, messages, config):
        self.calls.append((messages, config))
        if not self.script:
            raise AssertionError("unexpected judge call")
        return self.script.pop(0)


def _dataset(monkeypatch, tmp_path, rubric):
    row = {
        "id": SAMPLE_ID,
        "query": QUERY,
        "reference_answer": REFERENCE,
        "rubric": rubric,
        "do_not_train": True,
    }
    body = (json.dumps(row) + "\n").encode()
    spec = replace(
        DATASETS["bigfinance-public-50"],
        sha256=hashlib.sha256(body).hexdigest(),
        count=1,
        revision="offline-test",
    )
    monkeypatch.setitem(DATASETS, "bigfinance-public-50", spec)
    path = tmp_path / "data.jsonl"
    path.write_bytes(body)
    return path


def _text(message) -> str:
    content = message.content
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def _grade(monkeypatch, tmp_path, outputs, rubric, policy="full"):
    path = _dataset(monkeypatch, tmp_path, rubric)
    model = _Model(outputs)
    roles: list[str] = []

    def get_model(name, **kwargs):
        roles.append(kwargs.get("role") or "")
        return model

    monkeypatch.setattr(judging, "get_model", get_model)
    held: dict = {}
    invocations = []

    def agent(case):
        invocations.append(case)
        return SAVED_ANSWER

    def source(trace_id, case):
        evidence = complete_evidence(trace_id, case)
        held["dump"] = evidence.model_dump()
        held["evidence"] = evidence
        return evidence

    report = evaluate_sync(
        "bigfinance-public-50",
        agent,
        trace_source=CallbackTraceSource(source),
        allow_paid=True,
        data_path=path,
        judge_models=["mock/perfect"],
        content_policy=policy,
        limit=1,
    )
    held["invocations"] = invocations
    return report, model, held, roles


def _assert_agent_evidence_unchanged(report, held, policy="full"):
    result = report.results[0]
    assert len(held["invocations"]) == 1
    assert result.sample_id == SAMPLE_ID
    assert result.trace_id == held["evidence"].trace_id
    assert held["evidence"].model_dump() == held["dump"]
    assert len(held["evidence"].events) == 3
    if policy == "full":
        assert result.answer == SAVED_ANSWER
    with session_scope() as session:
        spans = list(session.scalars(select(Span).where(Span.trace_id == result.trace_id)))
    blob = json.dumps([span.content for span in spans], default=str)
    assert len(spans) == 3
    if policy == "full":
        assert TRACE_BODY in blob
    return blob


def _assert_repair_call(model):
    assert len(model.calls) == 2 and model.script == []
    first = "\n".join(_text(message) for message in model.calls[0][0])
    repair_messages = model.calls[1][0]
    repair = "\n".join(_text(message) for message in repair_messages)
    assert "financial-research" in first and REFERENCE in first and TRACE_BODY in first
    for forbidden in FORBIDDEN:
        assert forbidden not in repair
    payload = json.loads(_text(repair_messages[1]))
    assert set(payload) == {"invalid_output", "schema"}
    assert "protected" not in json.dumps(payload["schema"])
    for config in (model.calls[0][1], model.calls[1][1]):
        assert config.temperature == 0 and config.max_retries == 0


ONE = [{"text": CRITERION, "points": 2}]
TWO = [{"text": CRITERION, "points": 1}, {"text": "second protected criterion", "points": 3}]
MISSING = json.dumps(
    {
        "final_answer_correct": True,
        "rubric": [{"index": 1, "satisfied": True, "explanation": "JUDGE_MISSING_INDEX_TOKEN"}],
    }
)
TRUNCATED = '{"final_answer_correct": true, "rubric": ["JUDGE_TRUNCATED_TOKEN"'
MALFORMED = f"not-json {SECRET} JUDGE_TOKEN_NOT_JSON"


@pytest.mark.parametrize(
    ("name", "outputs", "rubric", "stage", "message", "token"),
    [
        (
            "malformed",
            [_Result(MALFORMED), _Result(f"still-not-json {SECRET} JUDGE_TOKEN_NOT_JSON")],
            ONE,
            "json_parse",
            "Evaluator did not return valid JSON.",
            "JUDGE_TOKEN_NOT_JSON",
        ),
        (
            "truncated",
            [_Result(TRUNCATED, stop_reason="max_tokens"), _Result(TRUNCATED, stop_reason="max_tokens")],
            ONE,
            "json_parse",
            "Evaluator JSON response was truncated.",
            "JUDGE_TRUNCATED_TOKEN",
        ),
        (
            "array",
            [_Result('["JUDGE_ARRAY_TOKEN"]'), _Result('["JUDGE_ARRAY_TOKEN"]')],
            ONE,
            "json_parse",
            "Evaluator response must be a JSON object.",
            "JUDGE_ARRAY_TOKEN",
        ),
        (
            "missing-index",
            [_Result(MISSING), _Result(MISSING)],
            TWO,
            "json_schema",
            "Judge did not grade every rubric criterion exactly once.",
            "JUDGE_MISSING_INDEX_TOKEN",
        ),
        (
            "provider",
            [_Result(error=f"upstream {SECRET} PROVIDER_BODY")],
            ONE,
            "provider",
            "Evaluator model returned an error.",
            "PROVIDER_BODY",
        ),
    ],
)
def test_grading_failures_persist_stage_and_keep_agent_evidence(
    name, outputs, rubric, stage, message, token, monkeypatch, tmp_path
):
    report, model, held, roles = _grade(monkeypatch, tmp_path, outputs, rubric)
    result = report.results[0]
    diagnostic = result.metadata["grading_diagnostic"]
    assert report.status == "failed" and report.metrics["mean_score"] is None
    assert result.status == "grading_error" and result.score is None and result.components == {}
    assert result.error == f"{stage}: {message}"
    assert diagnostic == {
        "stage": stage,
        "exception": "GradingError",
        "message": message,
        "repair_attempted": name != "provider",
    }
    assert set(diagnostic) == {"stage", "exception", "message", "repair_attempted"}
    blob = _assert_agent_evidence_unchanged(report, held)
    report_text = report.model_dump_json()
    artifact = read_sample_artifact(report.run_id, SAMPLE_ID, 1)
    artifact_text = json.dumps(artifact)
    assert token not in report_text and token not in artifact_text and token not in blob
    assert SECRET not in report_text and SECRET not in artifact_text and SECRET not in blob
    path = grading_diagnostic_path(report.run_id, SAMPLE_ID, 1)
    if name == "provider":
        assert len(model.calls) == 1 and not any("format_repair" in role for role in roles)
        assert TRACE_BODY in "\n".join(_text(message) for message in model.calls[0][0])
        assert model.calls[0][1].temperature == 0 and model.calls[0][1].max_retries == 0
        assert not path.exists()
    else:
        _assert_repair_call(model)
        assert sum("format_repair" in role for role in roles) == 1
        record = read_grading_diagnostic(report.run_id, SAMPLE_ID, 1)
        assert record["audience"] == "evaluator-only" and record["agent_evidence"] is False
        assert record["stage"] == stage and token in record["excerpt"] and SECRET not in record["excerpt"]
        assert path.stat().st_mode & 0o077 == 0
    with session_scope() as session:
        sample = session.scalar(select(SampleResult).where(SampleResult.run_id == report.run_id))
        assert sample.score is None and sample.outcome == "error" and sample.error == result.error
    if name == "malformed":
        from fastapi.testclient import TestClient

        from everybench.server import create_app

        with TestClient(create_app()) as client:
            url = f"/api/runs/{report.run_id}/grading-diagnostic"
            response = client.get(url, params={"sample_id": SAMPLE_ID})
            assert response.status_code == 200
            assert response.json()["agent_evidence"] is False
            assert token in response.json()["excerpt"] and SECRET not in response.text
            evidence = client.get(f"/api/runs/{report.run_id}/sample-evidence", params={"sample_id": SAMPLE_ID})
            assert evidence.status_code == 200 and token not in evidence.text and SECRET not in evidence.text
            assert client.get(url, params={"sample_id": "missing"}).status_code == 404
            with session_scope() as session:
                session.get(Run, report.run_id).workspace_id = "another-workspace"
            assert client.get(url, params={"sample_id": SAMPLE_ID}).status_code == 404


def test_metadata_policy_keeps_stage_and_withholds_judge_excerpt(monkeypatch, tmp_path, everybench_home):
    report, _, held, _ = _grade(
        monkeypatch,
        tmp_path,
        [_Result(MALFORMED), _Result(MALFORMED)],
        ONE,
        policy="metadata",
    )
    result = report.results[0]
    assert result.answer is None
    assert result.metadata["grading_diagnostic"]["stage"] == "json_parse"
    assert result.score is None and result.status == "grading_error"
    _assert_agent_evidence_unchanged(report, held, policy="metadata")
    record = read_grading_diagnostic(report.run_id, SAMPLE_ID, 1)
    assert record["excerpt"] is None and record["content_state"] == "withheld" and record["agent_evidence"] is False
    stored = "\n".join(path.read_text() for path in everybench_home.rglob("*.json"))
    assert SECRET not in stored and "JUDGE_TOKEN_NOT_JSON" not in stored


def test_one_repair_recovers_a_scored_adaptation_without_changing_agent_evidence(monkeypatch, tmp_path):
    valid = json.dumps(
        {
            "final_answer_correct": True,
            "rubric": [{"index": 1, "satisfied": True, "explanation": "Visible support only."}],
        }
    )
    report, model, held, roles = _grade(monkeypatch, tmp_path, [_Result("JUDGE_TOKEN_NOT_JSON"), _Result(valid)], ONE)
    result = report.results[0]
    assert report.status == "succeeded" and result.status == "scored" and result.score == 1
    assert result.error is None and "grading_diagnostic" not in result.metadata
    assert result.metadata["format_repair"] is True
    assert result.metadata["protocol"] == PROTOCOL
    assert result.answer == SAVED_ANSWER
    _assert_repair_call(model)
    assert sum("format_repair" in role for role in roles) == 1
    blob = _assert_agent_evidence_unchanged(report, held)
    assert "JUDGE_TOKEN_NOT_JSON" not in report.model_dump_json() and "JUDGE_TOKEN_NOT_JSON" not in blob
    assert not grading_diagnostic_path(report.run_id, SAMPLE_ID, 1).exists()
    grade = read_sample_artifact(report.run_id, SAMPLE_ID, 1)["content"]["grade"]["metadata"]
    assert grade["jury_mode"] == "single-judge-adaptation" and grade["format_repair"] is True
    assert grade["protocol"] == PROTOCOL and "JUDGE_TOKEN_NOT_JSON" not in json.dumps(grade)
    with session_scope() as session:
        run = session.get(Run, report.run_id)
        sample = session.scalar(select(SampleResult).where(SampleResult.run_id == report.run_id))
        assert run.manifest["evaluation_contract"]["description"]["official_parity"] is False
        assert sample.score == 1 and sample.outcome == "correct" and sample.error is None


def test_fenced_json_object_still_parses(monkeypatch):
    model = _Model([_Result(completion='```json\n{"ok": true}\n```')])
    monkeypatch.setattr(judging, "get_model", lambda *args, **kwargs: model)
    assert asyncio.run(judging.ask_json("mock/perfect", "finance_extractor", "Return JSON", {"x": 1})) == {"ok": True}
    assert len(model.calls) == 1 and model.calls[0][1].temperature == 0 and model.calls[0][1].max_retries == 0
