import time

import pytest
from fastapi.testclient import TestClient

from everyeval.auth import create_api_key, create_user, ensure_workspace, secrets_for, set_secret
from everyeval.db import Secret, session_scope
from everyeval.server import create_app

PASSWORD = "correct horse battery"


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("EVERYEVAL_AUTH", "1")
    monkeypatch.setenv("EVERYEVAL_SECRET_KEY", "test-master-key")
    create_user("alice@example.com", PASSWORD, "team-a")
    create_user("bob@example.com", PASSWORD, "team-b")
    create_user("val@example.com", PASSWORD, "team-a", role="viewer")


def logged_in(email: str) -> TestClient:
    client = TestClient(create_app(workers=1))
    client.__enter__()
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return client


def wait_done(client, run_id):
    for _ in range(300):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("succeeded", "failed"):
            return run
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_requests_without_credentials_are_rejected(auth_env):
    with TestClient(create_app()) as client:
        assert client.get("/api/runs").status_code == 401
        assert (
            client.post("/v1/traces", content=b"", headers={"content-type": "application/x-protobuf"}).status_code
            == 401
        )
        bad = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrong password"})
        assert bad.status_code == 401


def test_workspaces_are_isolated(auth_env):
    alice, bob = logged_in("alice@example.com"), logged_in("bob@example.com")
    try:
        [run] = alice.post("/api/runs", json={"benchmark": "toy-arith", "models": ["mock/strong"], "limit": 3}).json()
        done = wait_done(alice, run["id"])
        assert done["status"] == "succeeded"
        trace_id = alice.get(f"/api/runs/{run['id']}/samples").json()["items"][0]["trace_id"]
        assert alice.get(f"/api/traces/{trace_id}").status_code == 200

        assert bob.get("/api/runs").json() == []
        assert bob.get(f"/api/runs/{run['id']}").status_code == 404
        assert bob.get(f"/api/traces/{trace_id}").status_code == 404
        assert bob.post(f"/api/runs/{run['id']}/cancel").status_code == 404
        assert alice.get("/api/auth/me").json()["workspace"]["name"] == "team-a"
    finally:
        alice.__exit__(None, None, None)
        bob.__exit__(None, None, None)


def test_viewer_is_read_only(auth_env):
    viewer = logged_in("val@example.com")
    try:
        assert viewer.get("/api/runs").status_code == 200
        resp = viewer.post("/api/runs", json={"benchmark": "toy-arith", "models": ["mock/strong"]})
        assert resp.status_code == 403
    finally:
        viewer.__exit__(None, None, None)


def test_api_key_scopes_ingest_and_can_be_revoked(auth_env):
    with session_scope() as session:
        ws_id = ensure_workspace(session, "team-b").id
    key = create_api_key(ws_id, "sdk")
    body = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "0af7651916cd43dd8448eb211c80319c",
                                "spanId": "b7ad6b7169203331",
                                "name": "job",
                                "startTimeUnixNano": "1",
                                "endTimeUnixNano": "2",
                            }
                        ]
                    }
                ]
            }
        ]
    }
    with TestClient(create_app()) as client:
        headers = {"Authorization": f"Bearer {key}"}
        assert client.post("/v1/traces", json=body, headers=headers).status_code == 200
        assert [t["trace_id"] for t in client.get("/api/traces", headers=headers).json()] == [
            "0af7651916cd43dd8448eb211c80319c"
        ]
        keys = client.get("/api/keys", headers=headers).json()
        assert "key_hash" not in keys[0]
    alice = logged_in("alice@example.com")
    try:
        assert alice.get("/api/traces").json() == []  # other workspace
    finally:
        alice.__exit__(None, None, None)
    with TestClient(create_app()) as client:
        bob = logged_in("bob@example.com")
        try:
            assert bob.delete(f"/api/keys/{keys[0]['id']}").status_code == 200
        finally:
            bob.__exit__(None, None, None)
        assert client.get("/api/traces", headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_secrets_are_encrypted_and_scoped(auth_env):
    with session_scope() as session:
        a = ensure_workspace(session, "team-a").id
        b = ensure_workspace(session, "team-b").id
    set_secret(a, "OPENAI_API_KEY", "sk-team-a-secret")
    with session_scope() as session:
        stored = session.query(Secret).one()
        assert "sk-team-a-secret" not in stored.ciphertext
    assert secrets_for(a) == {"OPENAI_API_KEY": "sk-team-a-secret"}
    assert secrets_for(b) == {}
    alice = logged_in("alice@example.com")
    try:
        listed = alice.get("/api/secrets").json()
        assert listed[0]["name"] == "OPENAI_API_KEY" and "value" not in listed[0]
    finally:
        alice.__exit__(None, None, None)


def test_run_process_receives_workspace_secrets(auth_env):
    from everyeval.catalog import get_benchmark
    from everyeval.execution import _child_env
    from everyeval.service import create_runs

    with session_scope() as session:
        a = ensure_workspace(session, "team-a").id
    set_secret(a, "OPENAI_API_KEY", "sk-team-a-secret")
    [run_a] = create_runs(get_benchmark("toy-arith"), ["mock/strong"], workspace_id=a)
    [run_local] = create_runs(get_benchmark("toy-arith"), ["mock/strong"])
    assert _child_env(run_a.id)["OPENAI_API_KEY"] == "sk-team-a-secret"
    assert _child_env(run_local.id).get("OPENAI_API_KEY") != "sk-team-a-secret"
