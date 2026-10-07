"""The project was renamed from benchtrace. Settings, data, keys and traces from before still work."""

import hashlib

from fastapi.testclient import TestClient

from everyeval.auth import ensure_workspace
from everyeval.config import env, home_dir, settings
from everyeval.db import ApiKey, session_scope
from everyeval.otlp import CONTENT_KEY, span_kind
from everyeval.server import create_app


def test_old_environment_variables_are_a_fallback(monkeypatch):
    monkeypatch.setenv("BENCHTRACE_REQUIRE_QUOTE", "0")
    assert env("REQUIRE_QUOTE") == "0"
    monkeypatch.setenv("EVERYEVAL_REQUIRE_QUOTE", "1")
    assert env("REQUIRE_QUOTE") == "1"


def test_old_data_folder_is_used_until_the_new_one_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EVERYEVAL_HOME")
    monkeypatch.delenv("BENCHTRACE_HOME", raising=False)
    assert home_dir() == tmp_path / ".everyeval"
    (tmp_path / ".benchtrace").mkdir()
    (tmp_path / ".benchtrace" / "benchtrace.db").touch()
    assert home_dir() == tmp_path / ".benchtrace"
    assert settings().database_url.endswith("/.benchtrace/benchtrace.db")
    (tmp_path / ".everyeval").mkdir()
    assert home_dir() == tmp_path / ".everyeval"


def test_old_span_attributes_keep_their_meaning():
    assert span_kind({"benchtrace.span.kind": "tool"}, "lookup") == "tool"
    # Content under the old prefix must still be recognised, or the content policy would not redact it.
    assert CONTENT_KEY.match("benchtrace.content.prompt")


def test_api_keys_made_before_the_rename_still_work(monkeypatch):
    monkeypatch.setenv("EVERYEVAL_AUTH", "1")
    monkeypatch.setenv("EVERYEVAL_SECRET_KEY", "test-master-key")
    token = "bt_" + "x" * 43
    with session_scope() as session:
        ws_id = ensure_workspace(session, "team").id
        key_hash = hashlib.sha256(token.encode()).hexdigest()
        session.add(ApiKey(workspace_id=ws_id, name="old", prefix=token[:12], key_hash=key_hash))
    with TestClient(create_app()) as client:
        assert client.get("/api/runs", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert client.get("/api/runs", headers={"Authorization": "Bearer bt_unknown"}).status_code == 401
