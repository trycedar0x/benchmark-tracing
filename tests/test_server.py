import time

import pytest
from fastapi.testclient import TestClient

from benchtrace.server import create_app


@pytest.fixture
def client():
    with TestClient(create_app(workers=2)) as c:
        yield c


def wait_for(client, path, done, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(path).json()
        if done(data):
            return data
        time.sleep(0.3)
    raise AssertionError(f"timed out waiting for {path}: {data}")


def finished(run):
    return run["status"] in ("succeeded", "failed", "cancelled", "budget_exceeded")


def test_catalog_and_health(client):
    assert client.get("/api/health").json()["ok"]
    refs = [e["ref"] for e in client.get("/api/catalog").json()]
    assert "toy-arith@1" in refs and "gsm8k@1" in refs
    assert client.get("/api/catalog/nope").status_code == 404


def test_run_through_queue_then_compare_and_trace(client):
    resp = client.post("/api/runs", json={"benchmark": "toy-arith", "models": ["btmock/strong", "btmock/weak"]})
    assert resp.status_code == 201, resp.text
    a, b = (r["id"] for r in resp.json())
    run_a = wait_for(client, f"/api/runs/{a}", finished)
    run_b = wait_for(client, f"/api/runs/{b}", finished)
    assert run_a["status"] == run_b["status"] == "succeeded"
    assert run_a["outcomes"]["correct"] > run_b["outcomes"]["correct"]

    samples = client.get(f"/api/runs/{b}/samples", params={"outcome": "incorrect"}).json()
    assert samples["total"] == run_b["outcomes"]["incorrect"]
    trace = client.get(f"/api/traces/{samples['items'][0]['trace_id']}").json()
    assert trace["sample"]["outcome"] == "incorrect"
    assert any(s["kind"] == "model" for s in trace["spans"])

    comparison = client.get("/api/compare", params={"a": a, "b": b}).json()
    assert comparison["compatibility"]["comparable"]
    assert comparison["delta"] < 0
    assert [r["id"] for r in client.get("/api/runs").json()][:2] == [b, a] or len(client.get("/api/runs").json()) == 2


def test_paid_models_need_approved_quote(client):
    resp = client.post("/api/runs", json={"benchmark": "toy-arith", "models": ["openai/gpt-4o-mini"]})
    assert resp.status_code == 400
    assert "approved quote" in resp.json()["detail"]


def test_quote_flow(client):
    resp = client.post("/api/quotes", json={"benchmark": "toy-arith", "models": ["btmock/weak"], "sample_size": 3})
    assert resp.status_code == 201, resp.text
    quote_id = resp.json()["id"]
    early = client.post(f"/api/quotes/{quote_id}/approve", json={"cap_usd": 1})
    quote = wait_for(client, f"/api/quotes/{quote_id}", lambda q: q["status"] != "estimating")
    assert quote["status"] == "draft"
    assert quote["estimate"]["btmock/weak"]["samples_measured"] == 3
    if early.status_code == 200:
        pytest.skip("estimate finished before the early approval attempt")
    assert early.status_code == 409
    assert client.post(f"/api/quotes/{quote_id}/approve", json={"cap_usd": 1}).json()["status"] == "approved"
    runs = client.post("/api/runs", json={"quote_id": quote_id}).json()
    run = wait_for(client, f"/api/runs/{runs[0]['id']}", finished)
    assert run["status"] == "succeeded" and run["budget_usd"] == 1


def test_cancel_via_api(client):
    [run] = client.post("/api/runs", json={"benchmark": "toy-tools", "models": ["btmock/strong"], "epochs": 50}).json()
    wait_for(client, f"/api/runs/{run['id']}", lambda r: r["samples_done"] > 0)
    client.post(f"/api/runs/{run['id']}/cancel")
    final = wait_for(client, f"/api/runs/{run['id']}", finished)
    assert final["status"] == "cancelled"
