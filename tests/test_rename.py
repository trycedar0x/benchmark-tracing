"""Legacy settings, data, keys and traces still work after the everybench rename."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from everybench.auth import ensure_workspace
from everybench.config import env, home_dir, settings
from everybench.db import ApiKey, session_scope
from everybench.otlp import CONTENT_KEY, span_kind
from everybench.server import create_app


def test_old_environment_variables_are_a_fallback(monkeypatch):
    monkeypatch.setenv("BENCHTRACE_INGEST_CONTENT", "metadata")
    assert env("INGEST_CONTENT") == "metadata"
    monkeypatch.setenv("EVERYBENCH_INGEST_CONTENT", "1")
    assert env("INGEST_CONTENT") == "1"


def test_old_data_folder_is_used_until_the_new_one_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EVERYBENCH_HOME")
    monkeypatch.delenv("BENCHTRACE_HOME", raising=False)
    monkeypatch.delenv("EVERYBENCH_DATABASE_URL", raising=False)  # set when the suite runs on Postgres
    assert home_dir() == tmp_path / ".everybench"
    (tmp_path / ".benchtrace").mkdir()
    (tmp_path / ".benchtrace" / "benchtrace.db").touch()
    assert home_dir() == tmp_path / ".benchtrace"
    assert settings().database_url.endswith("/.benchtrace/benchtrace.db")
    (tmp_path / ".everybench").mkdir()
    assert home_dir() == tmp_path / ".everybench"


def test_old_span_attributes_keep_their_meaning():
    assert span_kind({"benchtrace.span.kind": "tool"}, "lookup") == "tool"
    # Content under the old prefix must still be recognised, or the content policy would not redact it.
    assert CONTENT_KEY.match("benchtrace.content.prompt")
    assert span_kind({"everyeval.span.kind": "model"}, "lookup") == "model"
    assert CONTENT_KEY.match("everyeval.content.prompt")


def test_api_keys_made_before_the_rename_still_work(monkeypatch):
    monkeypatch.setenv("EVERYBENCH_AUTH", "1")
    monkeypatch.setenv("EVERYBENCH_SECRET_KEY", "test-master-key")
    token = "bt_" + "x" * 43
    with session_scope() as session:
        ws_id = ensure_workspace(session, "team").id
        key_hash = hashlib.sha256(token.encode()).hexdigest()
        session.add(ApiKey(workspace_id=ws_id, name="old", prefix=token[:12], key_hash=key_hash))
    with TestClient(create_app()) as client:
        assert client.get("/api/runs", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert client.get("/api/runs", headers={"Authorization": "Bearer bt_unknown"}).status_code == 401


@pytest.mark.parametrize("name", ["AUTH", "SECRET_KEY", "DATABASE_URL", "INGEST_CONTENT", "HOME"])
def test_everyeval_environment_fallback_and_precedence(monkeypatch, name):
    monkeypatch.delenv(f"EVERYBENCH_{name}", raising=False)
    monkeypatch.setenv(f"BENCHTRACE_{name}", "oldest")
    monkeypatch.setenv(f"EVERYEVAL_{name}", "legacy")
    assert env(name) == "legacy"
    monkeypatch.setenv(f"EVERYBENCH_{name}", "current")
    assert env(name) == "current"
    monkeypatch.setenv(f"EVERYBENCH_{name}", "")
    assert env(name) == ""


def test_everyeval_data_and_database_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for prefix in ("EVERYBENCH", "EVERYEVAL", "BENCHTRACE"):
        for name in ("HOME", "DATABASE_URL"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)
    (tmp_path / ".benchtrace").mkdir()
    legacy = tmp_path / ".everyeval"
    legacy.mkdir()
    (legacy / "everyeval.db").touch()
    assert home_dir() == legacy
    assert settings().database_url == f"sqlite:///{legacy / 'everyeval.db'}"
    current = tmp_path / ".everybench"
    current.mkdir()
    assert home_dir() == current
    assert settings().database_url == f"sqlite:///{current / 'everybench.db'}"


def test_legacy_request_headers_are_accepted():
    from starlette.requests import Request

    from everybench.server import request_header

    request = Request({"type": "http", "headers": [(b"x-everyeval-workspace", b"legacy")]})
    assert request_header(request, "workspace") == "legacy"
    request = Request({
        "type": "http",
        "headers": [(b"x-everybench-workspace", b"current"), (b"x-everyeval-workspace", b"legacy")],
    })
    assert request_header(request, "workspace") == "current"
