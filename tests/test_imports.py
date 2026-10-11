"""Importers against fixtures shaped like each service's documented API responses."""

import json
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from everyeval.datasets import ReviewError, add_run_samples, add_traces, create_dataset, export_items, review_item
from everyeval.db import Span, Trace, session_scope
from everyeval.imports import create_import, execute_import

LANGFUSE_TRACE = {
    "id": "lf-trace-1",
    "name": "support-answer",
    "timestamp": "2026-10-01T10:00:00.000Z",
    "input": {"question": "How do I reset my password?"},
    "output": "Use the reset link.",
    "tags": ["prod"],
    "metadata": {"user_tier": "free"},
    "observations": [
        {
            "id": "obs-1",
            "traceId": "lf-trace-1",
            "type": "GENERATION",
            "name": "draft",
            "startTime": "2026-10-01T10:00:00.100Z",
            "endTime": "2026-10-01T10:00:01.100Z",
            "model": "gpt-4o-mini",
            "input": [{"role": "user", "content": "How do I reset my password?"}],
            "output": "Use the reset link.",
            "usageDetails": {"input": 12, "output": 6},
            "level": "DEFAULT",
            "parentObservationId": None,
        },
        {
            "id": "obs-2",
            "traceId": "lf-trace-1",
            "type": "TOOL",
            "name": "kb_search",
            "startTime": "2026-10-01T10:00:00.200Z",
            "endTime": "2026-10-01T10:00:00.400Z",
            "input": {"q": "reset password"},
            "output": "Article 12",
            "level": "ERROR",
            "statusMessage": "timeout",
            "parentObservationId": "obs-1",
        },
    ],
    "scores": [
        {
            "id": "sc-1",
            "name": "helpfulness",
            "value": 0.5,
            "comment": "vague",
            "observationId": None,
            "timestamp": "2026-10-01T10:05:00Z",
        }
    ],
}

LANGSMITH_RUNS = [
    {
        "id": "run-root",
        "trace_id": "trace-1",
        "parent_run_id": None,
        "name": "AgentExecutor",
        "run_type": "chain",
        "start_time": "2026-10-01T10:00:00",
        "end_time": "2026-10-01T10:00:03",
        "inputs": {"input": "2+2?"},
        "outputs": {"output": "4"},
        "error": None,
        "extra": {},
    },
    {
        "id": "run-llm",
        "trace_id": "trace-1",
        "parent_run_id": "run-root",
        "name": "ChatOpenAI",
        "run_type": "llm",
        "start_time": "2026-10-01T10:00:01",
        "end_time": "2026-10-01T10:00:02",
        "inputs": {"messages": []},
        "outputs": {"generations": []},
        "prompt_tokens": 9,
        "completion_tokens": 1,
        "extra": {"metadata": {"ls_model_name": "gpt-4o"}},
    },
]

BRAINTRUST_EVENTS = [
    {
        "id": "e1",
        "span_id": "s-root",
        "root_span_id": "s-root",
        "span_parents": None,
        "span_attributes": {"name": "eval-task", "type": "task"},
        "input": "Capital of France?",
        "output": "Paris",
        "expected": "Paris",
        "scores": {"exact": 1},
        "metrics": {"start": 1759312800.0, "end": 1759312801.5},
    },
    {
        "id": "e2",
        "span_id": "s-llm",
        "root_span_id": "s-root",
        "span_parents": ["s-root"],
        "span_attributes": {"name": "Chat Completion", "type": "llm"},
        "input": [{"role": "user", "content": "?"}],
        "output": "Paris",
        "metrics": {"start": 1759312800.2, "end": 1759312801.2, "prompt_tokens": 5, "completion_tokens": 1},
    },
]


def fake_fetch(calls):
    def fetch(method, url, headers, body):
        calls.append((method, url, headers, body))
        parsed = urlparse(url)
        path, query = parsed.path, parse_qs(parsed.query)
        if path == "/api/public/traces":
            assert headers["Authorization"].startswith("Basic ")
            return {"data": [{"id": "lf-trace-1"}], "meta": {"page": 1, "limit": 50, "totalItems": 1, "totalPages": 1}}
        if path == "/api/public/traces/lf-trace-1":
            return LANGFUSE_TRACE
        if path == "/api/v1/sessions":
            assert headers["x-api-key"] == "ls-key" and query["name"] == ["my-project"]
            return [{"id": "session-1", "name": "my-project"}]
        if path == "/api/v1/runs/query":
            assert body["session"] == ["session-1"]
            runs = [r for r in LANGSMITH_RUNS if body.get("is_root") is None or r["parent_run_id"] is None]
            return {"runs": runs, "cursors": {"next": None}}
        if path == "/v1/project":
            return {"objects": [{"id": "11111111-2222-3333-4444-555555555555", "name": "proj"}]}
        if path.endswith("/fetch"):
            assert headers["Authorization"] == "Bearer bt-key"
            return {"events": BRAINTRUST_EVENTS, "cursor": None}
        raise AssertionError(f"unexpected {method} {url}")

    return fetch


@pytest.fixture(autouse=True)
def creds(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.setenv("BRAINTRUST_API_KEY", "bt-key")


def imported(source, project=None, content="full", rights="own_data"):
    record = create_import(
        source, project=project, usage_rights=rights, content_policy=content, options={"max_traces": 10}
    )
    result = execute_import(record.id, fetch=fake_fetch([]))
    assert result.status == "succeeded", result.error
    with session_scope() as session:
        [trace] = session.scalars(select(Trace).where(Trace.source == f"import:{source}")).all()
        spans = session.scalars(select(Span).where(Span.trace_id == trace.trace_id)).all()
    return result, trace, {s.name: s for s in spans}


def test_langfuse_import_maps_observations_scores_and_errors():
    result, trace, spans = imported("langfuse")
    assert (result.traces_imported, result.spans_imported) == (1, 4)
    assert trace.source_ref == "lf-trace-1" and trace.state == "imported"
    assert spans["support-answer"].kind == "agent"
    assert spans["draft"].kind == "model" and spans["draft"].attributes["gen_ai.usage.input_tokens"] == 12
    assert spans["kb_search"].kind == "tool" and spans["kb_search"].status == "error"
    assert spans["kb_search"].parent_id == spans["draft"].span_id
    assert spans["helpfulness"].kind == "scorer" and spans["helpfulness"].attributes["score.value"] == 0.5


def test_reimport_replaces_instead_of_duplicating():
    imported("langfuse")
    record = create_import("langfuse", project=None, usage_rights="own_data", options={"max_traces": 10})
    execute_import(record.id, fetch=fake_fetch([]))
    with session_scope() as session:
        assert len(session.scalars(select(Trace)).all()) == 1
        assert len(session.scalars(select(Span)).all()) == 4


def test_langsmith_import_builds_run_tree():
    _, trace, spans = imported("langsmith", project="my-project")
    assert trace.source_ref == "trace-1"
    assert spans["AgentExecutor"].kind == "agent" and spans["AgentExecutor"].parent_id is None
    llm = spans["ChatOpenAI"]
    assert llm.kind == "model" and llm.parent_id == spans["AgentExecutor"].span_id
    assert llm.attributes["gen_ai.request.model"] == "gpt-4o" and llm.attributes["gen_ai.usage.output_tokens"] == 1


def test_braintrust_import_groups_by_root_span():
    _, trace, spans = imported("braintrust", project="proj")
    assert spans["eval-task"].kind == "agent" and spans["eval-task"].attributes["score.exact"] == 1
    assert spans["Chat Completion"].kind == "model"
    assert spans["eval-task"].content["expected"] == "Paris"


def test_metadata_policy_drops_imported_content():
    _, _, spans = imported("braintrust", project="proj", content="metadata")
    assert all(s.content is None for s in spans.values())


def test_missing_credentials_fail_clearly(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY")
    record = create_import("langsmith", project="p", usage_rights="own_data")
    result = execute_import(record.id, fetch=fake_fetch([]))
    assert result.status == "failed" and "LANGSMITH_API_KEY" in result.error


def test_usage_rights_must_be_declared():
    with pytest.raises(ValueError, match="usage rights"):
        create_import("langfuse", project=None, usage_rights="whatever")


def test_dataset_draft_review_and_export_from_import():
    _, trace, _ = imported("braintrust", project="proj")
    ds = create_dataset("support failures")
    [item] = add_traces(ds.id, [trace.trace_id])
    assert item.status == "draft" and item.input == "Capital of France?" and item.historical_output == "Paris"
    assert item.reference_output is None and item.usage_rights == "own_data"
    assert export_items(ds.id) == []  # drafts are never exported

    with pytest.raises(ReviewError, match="no reference answer"):
        review_item(item.id, reviewer="t", status="approved")
    review_item(item.id, reviewer="t", reference_output="Paris", split="test", status="approved")
    [row] = export_items(ds.id)
    assert row["input"] == "Capital of France?" and row["target"] == "Paris" and row["metadata"]["split"] == "test"
    assert add_traces(ds.id, [trace.trace_id]) == []  # no duplicates


def test_dataset_from_benchmark_failures():
    from everyeval.catalog import get_benchmark
    from everyeval.execution import execute_run
    from everyeval.service import create_runs

    [run] = create_runs(get_benchmark("toy-arith"), ["mock/weak"], limit=10)
    execute_run(run.id)
    ds = create_dataset("arith misses")
    items = add_run_samples(ds.id, run.id, ["incorrect"], split="dev")
    assert items and all(i.split == "dev" and i.input.startswith("What is") for i in items)
    assert all(i.usage_rights == "licensed" and i.provenance["outcome"] == "incorrect" for i in items)


def test_inspect_log_import_creates_comparable_run(tmp_path):
    import glob

    from everyeval.catalog import get_benchmark
    from everyeval.execution import execute_run
    from everyeval.service import create_runs

    [run] = create_runs(get_benchmark("toy-arith"), ["mock/strong"], limit=5)
    finished = execute_run(run.id)
    [log_path] = glob.glob(str(tmp_path / "home" / "logs" / run.id / "*.eval"))
    record = create_import(
        "inspect_log", project=None, usage_rights="own_data", content_policy="full", options={"path": log_path}
    )
    result = execute_import(record.id)
    assert result.status == "succeeded" and result.traces_imported == 5
    from everyeval.db import Run

    with session_scope() as session:
        imported_run = session.scalars(select(Run).where(Run.benchmark.like("external:%toy_arith"))).one()
        assert imported_run.n_correct == finished.n_correct and imported_run.samples_done == 5


def test_otlp_file_import(tmp_path):
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
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000001000000000",
                            }
                        ]
                    }
                ]
            }
        ]
    }
    path = tmp_path / "spans.jsonl"
    path.write_text(json.dumps(body) + "\n" + json.dumps(body) + "\n")
    record = create_import("otlp_file", project=None, usage_rights="own_data", options={"path": str(path)})
    result = execute_import(record.id)
    assert result.status == "succeeded" and result.traces_imported == 1
