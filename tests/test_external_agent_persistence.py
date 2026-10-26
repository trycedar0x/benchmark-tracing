import json

import pytest
from alembic import command
from sqlalchemy import MetaData, Table, create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from test_evaluation_engine import SyntheticBenchmark

from everybench.compare import check_compatibility, compare_runs
from everybench.db import Run, SampleResult, _sqlite_pragmas, now, session_scope
from everybench.evaluation import evaluate_sync
from everybench.migrate import alembic_config, upgrade


def test_sqlite_migration_preserves_existing_parent_samples_and_traces_with_fk_enforcement(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    event.listen(engine, "connect", _sqlite_pragmas)
    cfg = alembic_config(engine)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "c3e9a1f0d2b4")
        metadata = MetaData()
        runs = Table("runs", metadata, autoload_with=connection)
        samples = Table("samples", metadata, autoload_with=connection)
        traces = Table("traces", metadata, autoload_with=connection)
        spans = Table("spans", metadata, autoload_with=connection)
        connection.execute(
            runs.insert().values(
                id="run_legacy",
                created_at=now(),
                status="succeeded",
                benchmark="legacy",
                variant_key="abc",
                model="openai/old-model",
                agent="my.module:Agent",
                resolved_models=["old-revision"],
                epochs=1,
                content_policy="full",
                samples_done=1,
                n_correct=1,
                n_error=0,
                input_tokens=12,
                output_tokens=7,
                metrics={"accuracy": 1},
                manifest={"keep": "original"},
            )
        )
        connection.execute(
            samples.insert().values(
                run_id="run_legacy",
                sample_id="original",
                epoch=1,
                outcome="correct",
                score=1,
                answer="saved answer",
                trace_id="legacy-trace",
                input_tokens=12,
                output_tokens=7,
            )
        )
        connection.execute(
            traces.insert().values(
                trace_id="legacy-trace",
                run_id="run_legacy",
                sample_id="original",
                created_at=now(),
                source="inspect",
                span_count=1,
                state="closed",
                attributes={"saved": "header"},
            )
        )
        connection.execute(
            spans.insert().values(
                trace_id="legacy-trace",
                span_id="old-span",
                run_id="run_legacy",
                name="saved model",
                kind="model",
                status="ok",
                attributes={"saved": "attrs"},
                content={"output": "saved body"},
                content_state="present",
                source="inspect",
            )
        )
        before = {
            name: [dict(row._mapping) for row in connection.execute(text(f"SELECT * FROM {name}"))]
            for name in ("runs", "samples", "traces", "spans")
        }
    upgrade(engine)
    upgrade(engine)  # Reopening is idempotent.
    with engine.begin() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        for name, rows in before.items():
            after = [dict(row._mapping) for row in connection.execute(text(f"SELECT * FROM {name}"))]
            if name == "runs":
                assert after[0].pop("target_kind") == "model"
                assert json.loads(after[0].pop("target")) == {}
            assert after == rows
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO samples (run_id,sample_id,epoch,outcome,input_tokens,output_tokens) "
                    "VALUES ('missing','orphan',1,'correct',0,0)"
                )
            )
    engine.dispose()


def test_external_multi_model_comparison_and_legacy_incompatibility():
    a = evaluate_sync(SyntheticBenchmark(), lambda case: "A", allow_paid=True, limit=2)
    b = evaluate_sync(SyntheticBenchmark(score=0), lambda case: "B", allow_paid=True, limit=2)
    with session_scope() as session:
        for run_id in (a.run_id, b.run_id):
            session.get(Run, run_id).resolved_models = ["model-a", "model-b"]
    comparison = compare_runs(a.run_id, b.run_id)
    assert comparison.compatibility.comparable and comparison.n_scored_pairs == 2
    assert comparison.run_a["target_kind"] == "external_agent" and comparison.run_a["model"] is None
    with session_scope() as session:
        aa, bb = session.get(Run, a.run_id), session.get(Run, b.run_id)
        bb.target_kind = "model"
        bb.model = "legacy-model"
        compat = check_compatibility(aa, bb, {("original/0", 1)}, {("original/0", 1)})
    assert not compat.comparable and any("Target kinds differ" in item for item in compat.blocking)


def test_external_comparison_blocks_different_case_sets_and_grading_contract():
    a = evaluate_sync(SyntheticBenchmark(), lambda case: "A", allow_paid=True, limit=2)
    b = evaluate_sync(SyntheticBenchmark(), lambda case: "B", allow_paid=True, limit=1)
    assert not compare_runs(a.run_id, b.run_id).compatibility.comparable
    with session_scope() as session:
        aa, bb = session.get(Run, a.run_id), session.get(Run, b.run_id)
        bb.variant_key = aa.variant_key
        bb.manifest = {**aa.manifest, "evaluation_contract": {"protocol": "changed"}}
    assert not compare_runs(a.run_id, b.run_id).compatibility.comparable


def test_server_run_sample_evidence_and_cli_inspection_remain_functional():
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from everybench.cli import app
    from everybench.server import create_app

    report = evaluate_sync(
        SyntheticBenchmark(), lambda case: "completed", allow_paid=True, limit=1, content_policy="full"
    )
    with TestClient(create_app()) as client:
        detail = client.get(f"/api/runs/{report.run_id}")
        assert detail.status_code == 200
        data = detail.json()
        assert data["model"] is None and data["target_kind"] == "external_agent"
        assert data["target"] == report.target
        samples = client.get(f"/api/runs/{report.run_id}/samples").json()["items"]
        assert samples[0]["sample_id"] == "original/0" and samples[0]["score"] == 1
    runner = CliRunner()
    for command_args in (["runs"], ["show", report.run_id]):
        result = runner.invoke(app, command_args)
        assert result.exit_code == 0, result.output
    with session_scope() as session:
        sample = session.scalar(select(SampleResult).where(SampleResult.run_id == report.run_id))
        assert sample.answer == "completed"


@pytest.mark.parametrize("failures", [set(), {1}, {0, 1, 2}])
def test_external_fractional_score_is_primary_in_sdk_cli_api_and_comparison(failures, monkeypatch):
    import re

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from everybench.cli import app, console
    from everybench.server import create_app

    monkeypatch.setattr(console, "width", 240)

    async def agent(case):
        if int(case.sample_id.rsplit("/", 1)[-1]) in failures:
            raise RuntimeError("synthetic failure")
        return "answer"

    a = evaluate_sync(SyntheticBenchmark(score=0.5), agent, allow_paid=True)
    b = evaluate_sync(SyntheticBenchmark(score=0.5), agent, allow_paid=True)
    expected = 0.5 if len(failures) < 3 else None
    assert a.metrics["mean_score"] == expected
    assert a.metrics["scored"] == 3 - len(failures)
    with TestClient(create_app()) as client:
        listed = {r["id"]: r for r in client.get("/api/runs").json()}
        detail = client.get(f"/api/runs/{a.run_id}").json()
        for data in (listed[a.run_id], detail):
            assert data["score"] == expected
            assert data["accuracy"] == (0 if expected is not None else None)
            assert data["metrics"]["mean_score"] == expected
        samples = client.get(f"/api/runs/{a.run_id}/samples").json()["items"]
        assert sum(s["score"] is None for s in samples) == len(failures)
        comparison = client.get(f"/api/compare?a={a.run_id}&b={b.run_id}").json()
        assert comparison["mean_a"] == comparison["mean_b"] == expected
        assert comparison["n_scored_pairs"] == 3 - len(failures)
    runner = CliRunner()
    shown = runner.invoke(app, ["show", a.run_id])
    assert shown.exit_code == 0, shown.output
    assert re.search(r"\bscore\s+" + (r"50\.0%" if expected is not None else "–"), shown.output)
    assert "fully-correct accuracy" in shown.output
    listed = runner.invoke(app, ["runs"])
    assert listed.exit_code == 0, listed.output
    assert "Score" in listed.output
    if expected is not None:
        assert "50.0%" in listed.output
    else:
        assert "0.0%" not in listed.output
    compared = runner.invoke(app, ["compare", a.run_id, b.run_id, "--json"])
    assert compared.exit_code == 0, compared.output
    assert json.loads(compared.output)["mean_a"] == expected


def test_legacy_primary_accuracy_ignores_external_mean_score(monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from everybench.cli import app, console
    from everybench.server import create_app

    monkeypatch.setattr(console, "width", 240)
    with session_scope() as session:
        session.add(
            Run(
                id="run_legacy_summary",
                benchmark="legacy",
                variant_key="legacy-variant",
                model="legacy-model",
                target_kind="model",
                status="succeeded",
                samples_done=3,
                n_error=1,
                n_correct=1,
                metrics={"mean_score": 0.25},
            )
        )
    with TestClient(create_app()) as client:
        detail = client.get("/api/runs/run_legacy_summary").json()
        assert detail["accuracy"] == 0.5 and detail["score"] is None
        assert client.get("/api/runs").json()[0]["accuracy"] == 0.5
    runner = CliRunner()
    for args in (["show", "run_legacy_summary"], ["runs"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert "50.0%" in result.output and "25.0%" not in result.output
        assert "fully-correct accuracy" not in result.output
    assert "Accuracy" in runner.invoke(app, ["runs"]).output


@pytest.mark.parametrize("policy", ["metadata", "redacted", "full"])
def test_opaque_token_credentials_never_reach_public_identity_or_grading_contract(policy):
    from everybench.evaluation import CallableAgent, report_path

    credential_fields = {
        f"{prefix_variant}{suffix}": f"opaqueSyntheticCredential{index}_{prefix_variant}{suffix}91735"
        for index, prefix in enumerate(("api", "access", "auth", "bearer", "session", "refresh"))
        for prefix_variant in (prefix, prefix.upper(), prefix.title())
        for suffix in ("token", "Token", "TOKEN", "ToKeN")
    }
    credential_fields.update(
        {
            "tokenenv": "opaqueSyntheticEnvironment91735",
            "access_token_env": "opaqueSyntheticAccessEnvironment91735",
            "tokenizerCredential": "opaqueSyntheticTokenizerCredential91735",
            "customTokenConfig": "opaqueSyntheticCustomToken91735",
        }
    )
    secrets = list(credential_fields.values())
    reports, variants = [], []
    for token_limit in (512, 8192):
        public_config = {
            "strategy": "two-model",
            "maxTokens": token_limit,
            "TOKEN-BUDGET": 16384,
            "token.limit": 1024,
            "tokenizer": {"name": "fixture-tokenizer", **credential_fields},
            "tokenizerName": "fixture-tokenizer",
            "tokenizer-revision": "v1",
            "nested": {**credential_fields, "deeper": [{**credential_fields, "temperature": 0.5}]},
        }
        expected_config = {
            "strategy": "two-model",
            "maxTokens": token_limit,
            "TOKEN-BUDGET": 16384,
            "token.limit": 1024,
            "tokenizer": {"name": "fixture-tokenizer"},
            "tokenizerName": "fixture-tokenizer",
            "tokenizer-revision": "v1",
            "nested": {"deeper": [{"temperature": 0.5}]},
        }
        benchmark = SyntheticBenchmark(score=0.5)
        benchmark.describe = lambda config=public_config: {"protocol": "synthetic-v1", "grading": config}
        report = evaluate_sync(
            benchmark,
            CallableAgent(lambda case: "answer", name="public-agent", version="v1"),
            target_config=public_config,
            allow_paid=True,
            limit=1,
            content_policy=policy,
        )
        assert report.target["config"] == expected_config
        assert report.target["name"] == "public-agent" and report.target["version"] == "v1"
        assert report.metrics["mean_score"] == 0.5
        persisted_report = report_path(report.run_id).read_text()
        assert json.loads(persisted_report) == report.model_dump()
        with session_scope() as session:
            run = session.get(Run, report.run_id)
            assert run.target == report.target
            assert run.manifest["evaluation_contract"]["description"]["grading"] == expected_config
            assert run.metrics["mean_score"] == 0.5 and run.n_correct == 0
            persisted_identity = json.dumps(run.target) + json.dumps(run.manifest)
            variants.append(run.variant_key)
        for secret in secrets:
            assert secret not in report.model_dump_json()
            assert secret not in persisted_report
            assert secret not in persisted_identity
        reports.append(report)
    assert reports[0].target["config_sha256"] != reports[1].target["config_sha256"]
    assert variants[0] != variants[1]
