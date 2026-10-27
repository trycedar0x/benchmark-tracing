"""Authentication-boundary and child credential isolation regressions."""

import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_backend_queue import REQUEST, queued, run_job
from test_backend_queue import backend as backend

from everybench.auth import create_api_key, create_user, ensure_workspace, set_secret
from everybench.db import Job, Run, session_scope
from everybench.execution import _child_env
from everybench.server import create_app


@pytest.mark.parametrize(
    "peer,host,headers",
    [
        ("192.0.2.1", "127.0.0.1", {}),
        ("127.0.0.1", "evil.example", {}),
        ("127.0.0.1", "127.0.0.1", {"origin": "https://evil.example"}),
        ("127.0.0.1", "127.0.0.1", {"origin": "null"}),
        ("127.0.0.1", "127.0.0.1", {"sec-fetch-site": "cross-site"}),
        ("192.0.2.1", "127.0.0.1", {"x-forwarded-for": "127.0.0.1", "x-forwarded-host": "localhost"}),
    ],
)
def test_auth_off_remote_and_cross_origin_denied_before_effects(backend, peer, host, headers):
    with TestClient(create_app(), base_url=f"http://{host}", client=(peer, 1234)) as client:
        for path in ("/api/catalog", "/api/evaluation-targets"):
            assert client.get(path, headers=headers).status_code == 403
        assert client.post("/api/runs", json=REQUEST, headers=headers).status_code == 403
    with session_scope() as session:
        assert session.scalar(select(func.count(Run.id))) == 0
    assert not backend["calls"]


def test_local_same_origin_allowed_and_null_profile_is_not_public(backend):
    assert backend["client"].get("/api/evaluation-targets", headers={"origin": "http://127.0.0.1"}).status_code == 200
    backend["path"].write_text(json.dumps([backend["profile"] | {"workspace_id": "another-workspace"}]))
    assert backend["client"].get("/api/evaluation-targets").json() == []
    assert backend["client"].post("/api/runs", json=REQUEST).status_code == 404


@pytest.fixture
def workspaces(backend, monkeypatch):
    monkeypatch.setenv("EVERYBENCH_AUTH", "1")
    for name in ("alpha", "beta"):
        create_user(f"{name}@example.test", "correct horse battery", name)
    create_user("viewer@example.test", "correct horse battery", "alpha", role="viewer")
    with session_scope() as session:
        a = ensure_workspace(session, "alpha").id
        b = ensure_workspace(session, "beta").id
    profiles = [
        backend["profile"],
        backend["profile"] | {"workspace_id": a},
        backend["profile"] | {"workspace_id": b},
        backend["profile"] | {"id": "hidden", "workspace_id": b},
    ]
    backend["path"].write_text(json.dumps(profiles))
    return {
        "a": a,
        "b": b,
        "a_key": create_api_key(a, "member"),
        "b_key": create_api_key(b, "member"),
        "viewer": create_api_key(a, "viewer", role="viewer"),
    }


def headers(key):
    return {"authorization": f"Bearer {key}"}


def test_api_key_workspace_is_stored_and_cannot_be_overridden(backend, workspaces):
    client = backend["client"]
    auth = headers(workspaces["a_key"]) | {"x-everybench-workspace": workspaces["b"]}
    response = client.post("/api/runs", json=REQUEST, headers=auth)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["workspace_id"] == workspaces["a"]
    with session_scope() as session:
        stored = session.get(Run, run["id"])
        assert stored.manifest["evaluation_submission"]["workspace_id"] == workspaces["a"]
        assert session.scalar(select(func.count(Job.id))) == 1
    assert client.get(f"/api/runs/{run['id']}", headers=headers(workspaces["b_key"])).status_code == 404
    unknown = client.post("/api/runs", json=REQUEST | {"target_id": "unknown"}, headers=auth)
    hidden = client.post("/api/runs", json=REQUEST | {"target_id": "hidden"}, headers=auth)
    assert unknown.status_code == hidden.status_code == 404 and unknown.json() == hidden.json()


def test_viewers_and_invalid_keys_rejected_before_secrets_or_rows(backend, workspaces, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Submission must not resolve secrets")

    monkeypatch.setattr("everybench.auth.secrets_for", forbidden)
    client = backend["client"]
    for key, expected in ((None, 401), ("ee_invalid", 401), (workspaces["viewer"], 403)):
        response = client.post("/api/runs", json=REQUEST, headers=headers(key) if key else {})
        assert response.status_code == expected
    assert client.get("/api/catalog", headers=headers(workspaces["viewer"])).status_code == 200
    assert client.get("/api/evaluation-targets", headers=headers(workspaces["viewer"])).status_code == 200
    with session_scope() as session:
        assert session.scalar(select(func.count(Run.id))) == session.scalar(select(func.count(Job.id))) == 0


def test_session_hint_cannot_select_nonmember_workspace(backend, workspaces):
    client = backend["client"]
    assert (
        client.post(
            "/api/auth/login", json={"email": "alpha@example.test", "password": "correct horse battery"}
        ).status_code
        == 200
    )
    response = client.post("/api/runs", json=REQUEST, headers={"x-everybench-workspace": workspaces["b"]})
    assert response.status_code == 201
    assert response.json()["workspace_id"] == workspaces["a"]


def test_scoped_same_name_secrets_resolved_only_in_children(backend, workspaces, monkeypatch):
    set_secret(workspaces["a"], "AGENT_TOKEN", "alpha-secret")
    set_secret(workspaces["b"], "AGENT_TOKEN", "beta-secret")
    # These must neither leak into children as evaluator credentials nor change in the parent.
    monkeypatch.setenv("OPENAI_API_KEY", "host-provider-secret")
    monkeypatch.setenv("AGENT_TOKEN", "host-agent-secret")
    before = dict(os.environ)
    for key, expected in ((workspaces["a_key"], "alpha-secret"), (workspaces["b_key"], "beta-secret")):
        response = backend["client"].post("/api/runs", json=REQUEST, headers=headers(key))
        assert response.status_code == 201
        child_env = _child_env(response.json()["id"])
        assert "AGENT_TOKEN" not in child_env and "OPENAI_API_KEY" not in child_env
        run_job()
        assert backend["calls"][-1]["headers"]["Authorization"] == f"Bearer {expected}"
    assert dict(os.environ) == before
    assert len(backend["calls"]) == 2


def test_missing_workspace_secret_never_falls_back_to_null_or_other_workspace(backend, workspaces):
    set_secret(workspaces["b"], "AGENT_TOKEN", "beta-secret")
    response = backend["client"].post("/api/runs", json=REQUEST, headers=headers(workspaces["a_key"]))
    assert response.status_code == 201
    run_job()
    final = backend["client"].get(f"/api/runs/{response.json()['id']}", headers=headers(workspaces["a_key"])).json()
    assert final["status"] == "failed" and "credentials unavailable" in final["error"]
    assert backend["calls"] == []


def test_submission_workspace_tampering_fails_closed(backend):
    run = queued(backend)
    with session_scope() as session:
        stored = session.get(Run, run["id"])
        stored.workspace_id = "different"
    run_job()
    assert not backend["calls"]
    with session_scope() as session:
        assert session.get(Run, run["id"]).status == "failed"


def test_agent_and_http_trace_credentials_are_independent(backend):
    backend["path"].write_text(
        json.dumps(
            [
                backend["profile"]
                | {"trace": {"type": "http", "url": backend["profile"]["url"], "token_secret": "TRACE_TOKEN"}}
            ]
        )
    )
    set_secret(None, "TRACE_TOKEN", "trace-only-secret")
    run = queued(backend, trace_timeout=0.1)
    run_job()
    assert backend["calls"][0]["headers"]["Authorization"] == "Bearer local-agent-secret"
    assert backend["trace_calls"] and all(
        c["headers"]["Authorization"] == "Bearer trace-only-secret" for c in backend["trace_calls"]
    )
    final = backend["client"].get(f"/api/runs/{run['id']}").json()
    assert final["status"] == "succeeded"
    assert "trace-only-secret" not in json.dumps(final) and "TRACE_TOKEN" not in json.dumps(final)


def test_missing_independent_trace_secret_fails_before_agent(backend):
    backend["path"].write_text(
        json.dumps(
            [
                backend["profile"]
                | {"trace": {"type": "http", "url": backend["profile"]["url"], "token_secret": "TRACE_TOKEN"}}
            ]
        )
    )
    run = queued(backend)
    run_job()
    assert not backend["calls"] and not backend["trace_calls"]
    assert backend["client"].get(f"/api/runs/{run['id']}").json()["status"] == "failed"


def test_revoked_api_key_cannot_submit(backend, workspaces):
    from everybench.db import ApiKey, now

    with session_scope() as session:
        key = session.scalar(select(ApiKey).where(ApiKey.workspace_id == workspaces["a"], ApiKey.name == "member"))
        key.revoked_at = now()
    assert backend["client"].post("/api/runs", json=REQUEST, headers=headers(workspaces["a_key"])).status_code == 401
    with session_scope() as session:
        assert session.scalar(select(func.count(Run.id))) == session.scalar(select(func.count(Job.id))) == 0


def test_concurrent_workspace_children_do_not_share_secrets(backend, workspaces, monkeypatch):
    import threading

    set_secret(workspaces["a"], "AGENT_TOKEN", "alpha-secret")
    set_secret(workspaces["b"], "AGENT_TOKEN", "beta-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "parent-only-secret")
    before = dict(os.environ)
    expected = {}
    for key, secret in ((workspaces["a_key"], "alpha-secret"), (workspaces["b_key"], "beta-secret")):
        response = backend["client"].post("/api/runs", json=REQUEST, headers=headers(key))
        assert response.status_code == 201
        expected[response.json()["id"]] = "Bearer " + secret
    errors = []

    def work():
        try:
            run_job()
        except Exception as ex:
            errors.append(ex)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert not errors and all(not thread.is_alive() for thread in threads)
    actual = {call["headers"]["x-everybench-run-id"]: call["headers"]["Authorization"] for call in backend["calls"]}
    assert actual == expected and dict(os.environ) == before
