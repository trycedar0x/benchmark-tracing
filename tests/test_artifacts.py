import json

import pytest
from fastapi.testclient import TestClient
from historical import historical_run
from inspect_ai.log import EvalSample
from inspect_ai.model import ModelOutput

from everybench.artifacts import read_sample_artifact, sample_artifact_path, write_sample_artifact
from everybench.db import Run, session_scope
from everybench.ingest import store_sample
from everybench.inspect_convert import convert_sample


@pytest.mark.parametrize("policy", ["metadata", "redacted", "full"])
def test_evidence_policy_is_applied_before_disk(policy):
    text = "x" * 10000 + " sk-abcdefghijklmnopqrstuvwx"
    write_sample_artifact("run", "../sample", 1, {"output": text}, policy)
    body = sample_artifact_path("run", "../sample", 1).read_text()
    record = read_sample_artifact("run", "../sample", 1)
    if policy == "metadata":
        assert record["content"] is None
        assert "xxxx" not in body and "sk-abcdef" not in body
    elif policy == "redacted":
        assert "sk-abcdef" not in body and "REDACTED" in body
        assert len(record["content"]["output"]) > 10000
    else:
        assert record["content"]["output"] == text
    assert sample_artifact_path("../../run", "../sample", 1).parent.name != "run"


def test_evidence_is_idempotently_replaced_and_private():
    write_sample_artifact("run", "sample", 1, {"output": "old"}, "full")
    write_sample_artifact("run", "sample", 1, {"output": "new"}, "metadata")
    path = sample_artifact_path("run", "sample", 1)
    assert json.loads(path.read_text())["content"] is None
    assert path.stat().st_mode & 0o077 == 0
    assert not list(path.parent.glob(".evidence-*"))


def test_sample_evidence_api_checks_workspace_and_handles_missing():
    run = historical_run()
    sample = EvalSample(
        id="sample/1",
        epoch=1,
        input="q",
        target="a",
        metadata={},
        scores={"match": {"value": 1}},
        output=ModelOutput.from_content("mock/strong", "complete " + "x" * 10000),
    )
    with session_scope() as session:
        store_sample(session, session.get(Run, run.id), convert_sample(sample, run.id))
    from everybench.server import create_app

    with TestClient(create_app()) as client:
        path = f"/api/runs/{run.id}/sample-evidence"
        response = client.get(path, params={"sample_id": "sample/1"})
        assert response.status_code == 200
        assert len(response.json()["content"]["output"]) > 10000
        assert client.get(path, params={"sample_id": "missing"}).status_code == 404
        assert client.get(path, params={"sample_id": "sample/1", "epoch": 0}).status_code == 422
        with session_scope() as session:
            session.get(Run, run.id).workspace_id = "another-workspace"
        assert client.get(path, params={"sample_id": "sample/1"}).status_code == 404
