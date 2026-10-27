"""Evaluation metadata/submission API, plus retained historical read contracts."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from test_backend_queue import REQUEST, queued, run_job
from test_backend_queue import backend as backend

from everybench.db import Job, Run, SampleResult, Span, Trace, session_scope


def test_catalog_and_health(backend):
    client = backend["client"]
    assert client.get("/api/health").json()["ok"]
    catalog = client.get("/api/catalog").json()
    assert {e["id"] for e in catalog} == {"findeepindicator", "bigfinance-public-50"}
    assert all(e["experimental"] and not e["official_parity"] and e["license"] for e in catalog)
    assert [e["count"] for e in catalog] == [1, 50]  # Synthetic pinned test data, never public metadata.
    assert client.get("/api/catalog/findeepindicator").status_code == 200
    assert client.get("/api/catalog/toy-arith").status_code == 404
    for route in ("/api/models", "/api/quotes", "/api/quotes/unknown"):
        assert client.get(route).status_code == 404
        assert client.post(route, json={}).status_code == 404
    targets = client.get("/api/evaluation-targets").json()
    assert targets == [
        {
            "id": "agent",
            "name": "Owned agent",
            "version": "v1",
            "origin": backend["profile"]["url"].rsplit("/", 1)[0],
            "has_trace_source": False,
        }
    ]
    assert "private-capability" not in str(targets) and "AGENT_TOKEN" not in str(targets)


@pytest.mark.parametrize(
    "updates",
    [
        {"allow_paid": False},
        {"allow_paid": "true"},
        {"allow_paid": 1},
        {"limit": 0},
        {"limit": True},
        {"timeout": 0},
        {"timeout": 3601},
        {"trace_timeout": -1},
        {"grading_timeout": 0},
        {"content_policy": "bad"},
        {"sample_ids": []},
        {"sample_ids": ["a", "a"]},
        {"url": "http://127.0.0.1"},
        {"workspace_id": "other"},
        {"token_secret": "OTHER_TOKEN"},
        {"models": ["openai/solver"]},
        {"data_path": "/tmp/private"},
        {"epochs": 2},
        {"quote_id": "old"},
        {"judge_models": []},
    ],
)
def test_strict_rejections_create_nothing(backend, updates, monkeypatch):
    from everybench.finance import judging

    def forbidden(*args, **kwargs):
        raise AssertionError("Rejected API requests must never invoke providers")

    monkeypatch.setattr(judging, "evaluator_model", forbidden)
    monkeypatch.setattr(judging, "ask_json", forbidden)
    response = backend["client"].post("/api/runs", json=REQUEST | updates)
    assert response.status_code == 422, response.text
    assert not backend["calls"]
    with session_scope() as session:
        assert session.scalar(select(func.count(Run.id))) == session.scalar(select(func.count(Job.id))) == 0


def test_missing_consent_and_incompatible_grading(backend):
    without_consent = {k: v for k, v in REQUEST.items() if k != "allow_paid"}
    assert backend["client"].post("/api/runs", json=without_consent).status_code == 422
    for updates in (
        {"benchmark": "unknown"},
        {"target_id": "unknown"},
        {"extractor_model": None},
        {"judge_models": ["mock/perfect", "mock/perfect"]},
        {"judge_models": ["unknown/model"]},
        {"benchmark": "bigfinance-public-50", "extractor_model": None},
    ):
        response = backend["client"].post("/api/runs", json=REQUEST | updates)
        assert response.status_code in (400, 404), response.text
    with session_scope() as session:
        assert session.scalar(select(func.count(Run.id))) == session.scalar(select(func.count(Job.id))) == 0
    assert not backend["calls"]


def test_timestamps_carry_utc_offset(backend):
    run = queued(backend)
    run_job()
    final = backend["client"].get(f"/api/runs/{run['id']}").json()
    listed = backend["client"].get("/api/runs").json()[0]
    for value in (final["created_at"], final["started_at"], final["finished_at"], listed["created_at"]):
        assert datetime.fromisoformat(value).utcoffset() == timedelta(0)


def test_legacy_history_comparison_traces_and_evidence_stay_readable(backend):
    from everybench.artifacts import write_sample_artifact
    from everybench.db import now

    with session_scope() as session:
        a = Run(
            benchmark="old-benchmark",
            model="old/a",
            variant_key="same",
            status="succeeded",
            samples_done=1,
            samples_total=1,
            n_correct=1,
        )
        b = Run(
            benchmark="old-benchmark",
            model="old/b",
            variant_key="same",
            status="succeeded",
            samples_done=1,
            samples_total=1,
            n_correct=0,
        )
        session.add_all([a, b])
        session.flush()
        session.add_all(
            [
                SampleResult(run_id=a.id, sample_id="old", outcome="correct", score=1),
                SampleResult(run_id=b.id, sample_id="old", outcome="incorrect", score=0, trace_id="a" * 32),
            ]
        )
        session.add(Trace(trace_id="a" * 32, run_id=b.id, sample_id="old", span_count=1))
        session.add(
            Span(
                trace_id="a" * 32,
                span_id="b" * 16,
                run_id=b.id,
                name="historical model",
                kind="model",
                start_time=now(),
                end_time=now(),
            )
        )
    write_sample_artifact(b.id, "old", 1, {"historical": "retained"}, "full")
    client = backend["client"]
    assert client.get(f"/api/runs/{a.id}").json()["model"] == "old/a"
    assert len(client.get("/api/runs").json()) == 2
    assert client.get(f"/api/runs/{b.id}/samples?outcome=incorrect").json()["total"] == 1
    trace = client.get(f"/api/traces/{'a' * 32}").json()
    assert trace["sample"]["outcome"] == "incorrect" and trace["spans"][0]["kind"] == "model"
    assert datetime.fromisoformat(trace["trace"]["created_at"]).utcoffset() == timedelta(0)
    assert client.get(f"/api/runs/{b.id}/sample-evidence?sample_id=old").status_code == 200
    comparison = client.get(f"/api/compare?a={a.id}&b={b.id}").json()
    assert comparison["compatibility"]["comparable"] and comparison["delta"] == -1
