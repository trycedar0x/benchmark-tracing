import asyncio
import csv
import hashlib
import io
import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_agent_transports import CONVERSATION, DELTA, DONE, local_server, ndjson, response
from test_evaluation_engine import complete_evidence
from typer.testing import CliRunner

from everybench.cli import app
from everybench.evaluation import FinanceBenchmark, HttpAgent, HTTPTraceSource, evaluate, evaluate_sync
from everybench.evaluation.types import AgentInput
from everybench.finance import judging
from everybench.finance.datasets import DATASETS

INDICATOR = {
    "id": "original-17",
    "question": "Compute the ratio.",
    "answer": "12.5",
    "formula": "profit/revenue",
    "data": "year,profit,revenue\n2024,1,8\n",
    "data_indicator": "",
}
BIG = {
    "id": "original-big-42",
    "query": "Research earnings.",
    "reference_answer": "protected reference",
    "rubric": [{"text": "protected criterion", "points": 2}],
    "do_not_train": True,
}


def pinned_data(monkeypatch, tmp_path, name):
    row = BIG if name == "bigfinance-public-50" else INDICATOR
    if name == "findeepindicator":
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        body = stream.getvalue().encode()
    else:
        body = json.dumps(row).encode()
    spec = replace(DATASETS[name], sha256=hashlib.sha256(body).hexdigest(), count=1, revision="offline-test")
    monkeypatch.setitem(DATASETS, name, spec)
    path = tmp_path / ("data." + spec.format)
    path.write_bytes(body)
    return path


async def fake_judge(model, role, instruction, evidence):
    assert model in ("mock/strong", "mock/perfect")
    if role == "finance_extractor":
        assert "protected" not in evidence["response"]
        return {"answer": "12.5", "formula": None, "data": None, "data_indicator": None}
    if role.startswith("finance_rubric_judge_"):
        assert "Actual captured model body" in json.dumps(evidence)
        return {"final_answer_correct": True, "rubric": [{"index": 1, "satisfied": True, "explanation": "yes"}]}
    raise AssertionError(role)


@pytest.mark.parametrize("name", ["findeepindicator", "bigfinance-public-50"])
def test_named_adapters_real_http_separate_authorized_trace_and_fake_judging(monkeypatch, tmp_path, name):
    from everybench.evaluation import CallbackTraceSource

    path = pinned_data(monkeypatch, tmp_path, name)
    monkeypatch.setattr(judging, "ask_json", fake_judge)
    inputs = []

    async def scenario():
        async def handler(request, reader, writer):
            headers = request["headers"]
            assert headers["authorization"] == "Bearer independent-agent-token"
            assert json.loads(request["body"]) == {
                "message": BIG["query"] if name.startswith("big") else INDICATOR["question"]
            }
            trace_id = headers["traceparent"].split("-")[1]
            inputs.append(
                AgentInput(
                    input=json.loads(request["body"])["message"],
                    run_id=headers["x-everybench-run-id"],
                    sample_id=headers["x-everybench-sample-id"],
                    attempt=int(headers["x-everybench-attempt"]),
                    trace_id=trace_id,
                    parent_span_id=headers["traceparent"].split("-")[2],
                )
            )
            writer.write(response(ndjson(CONVERSATION, DELTA, DONE), headers=f"x-everybench-trace-id: {trace_id}\r\n"))
            await writer.drain()

        async with local_server(handler) as (url, requests):
            report = await evaluate(
                name,
                HttpAgent(
                    url + "/api/chat", name="actual finance agent", version="v1", api_key="independent-agent-token"
                ),
                trace_source=CallbackTraceSource(complete_evidence),
                allow_paid=True,
                data_path=path,
                judge_models=["mock/perfect"],
                extractor_model="mock/strong" if name == "findeepindicator" else None,
                content_policy="full",
                limit=1,
            )
        assert len(requests) == 1
        return report

    report = asyncio.run(scenario())
    assert report.results[0].status == "scored", report.model_dump_json()
    assert (
        report.results[0].score == 1
        and report.results[0].sample_id == (BIG if name.startswith("big") else INDICATOR)["id"]
    )
    assert report.metrics["cost_usd"] is None
    assert len(inputs) == 1 and "protected" not in inputs[0].model_dump_json()


def test_real_http_trace_source_association_and_separate_auth(monkeypatch, tmp_path):
    path = pinned_data(monkeypatch, tmp_path, "bigfinance-public-50")
    monkeypatch.setattr(judging, "ask_json", fake_judge)
    captured = []

    async def scenario():
        async def agent_handler(request, reader, writer):
            headers = request["headers"]
            assert headers["authorization"] == "Bearer agent-token"
            captured.append(
                AgentInput(
                    input=BIG["query"],
                    run_id=headers["x-everybench-run-id"],
                    sample_id=headers["x-everybench-sample-id"],
                    trace_id=headers["traceparent"].split("-")[1],
                    parent_span_id=headers["traceparent"].split("-")[2],
                )
            )
            writer.write(response(ndjson(CONVERSATION, DELTA, DONE)))
            await writer.drain()

        async def trace_handler(request, reader, writer):
            assert request["headers"]["authorization"] == "Bearer trace-token"
            case = captured[0]
            events = complete_evidence(case.trace_id, case).events
            spans = [
                {
                    "trace_id": case.trace_id,
                    "span_id": event.id,
                    "parent_id": event.parent_id,
                    "kind": event.kind,
                    "name": event.name,
                    "attributes": event.attributes,
                    "content": event.content,
                    "content_state": "present",
                }
                for event in events
            ]
            payload = {
                "trace": {
                    "trace_id": case.trace_id,
                    "run_id": case.run_id,
                    "sample_id": case.sample_id,
                    "workspace_id": None,
                    "state": "closed",
                    "span_count": len(spans),
                    "attributes": {"expected_spans": len(spans), "sealed_at": "2026-01-01T00:00:00Z"},
                },
                "spans": spans,
            }
            writer.write(response(json.dumps(payload).encode(), content_type="application/json"))
            await writer.drain()

        async with (
            local_server(agent_handler) as (agent_url, requests),
            local_server(trace_handler) as (trace_url, traces),
        ):
            report = await evaluate(
                "bigfinance-public-50",
                HttpAgent(agent_url, api_key="agent-token"),
                allow_paid=True,
                trace_source=HTTPTraceSource(trace_url, api_key="trace-token"),
                data_path=path,
                judge_models=["mock/perfect"],
                content_policy="full",
            )
            assert len(requests) == 1 and len(traces) == 1
        return report

    report = asyncio.run(scenario())
    assert report.results[0].status == "scored", report.model_dump_json()


def test_cli_explicit_consent_guard_never_calls_engine(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("engine called without consent")

    monkeypatch.setattr("everybench.evaluation.evaluate_sync", forbidden)
    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "findeepindicator",
            "--url",
            "http://127.0.0.1:8000/api/chat",
            "--name",
            "agent",
            "--judge",
            "mock/perfect",
            "--extractor",
            "mock/strong",
        ],
    )
    assert result.exit_code == 1 and "--yes" in result.output


def test_cli_and_sdk_share_report_schema(monkeypatch, tmp_path):
    path = pinned_data(monkeypatch, tmp_path, "findeepindicator")
    monkeypatch.setattr(judging, "ask_json", fake_judge)
    calls = []

    async def invoke(self, case):
        from everybench.evaluation import AgentResult

        calls.append(case)
        return AgentResult(answer="submitted answer")

    monkeypatch.setattr(HttpAgent, "invoke", invoke)
    args = [
        "evaluate",
        "findeepindicator",
        "--url",
        "http://127.0.0.1:8000/api/chat",
        "--name",
        "actual-agent",
        "--version",
        "v7",
        "--judge",
        "mock/perfect",
        "--extractor",
        "mock/strong",
        "--data-path",
        str(path),
        "--sample-id",
        INDICATOR["id"],
        "--limit",
        "1",
        "--yes",
        "--json",
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    cli_report = json.loads(result.stdout)
    sdk_report = evaluate_sync(
        "findeepindicator",
        HttpAgent("http://127.0.0.1:8000/api/chat", name="actual-agent", version="v7"),
        allow_paid=True,
        judge_models=["mock/perfect"],
        extractor_model="mock/strong",
        data_path=path,
        sample_ids=[INDICATOR["id"]],
        limit=1,
    ).model_dump()
    assert set(cli_report) == set(sdk_report)
    assert cli_report["target"] == sdk_report["target"] and cli_report["metrics"] == sdk_report["metrics"]
    for report in (cli_report, sdk_report):
        report.pop("run_id")
        for case in report["results"]:
            case.pop("trace_id")
            case.pop("duration_seconds")
    assert cli_report == sdk_report and len(calls) == 2


def test_invalid_pinned_dataset_rejected_before_invocation(monkeypatch, tmp_path):
    path = pinned_data(monkeypatch, tmp_path, "findeepindicator")
    path.write_bytes(path.read_bytes() + b"tampered")
    calls = []
    with pytest.raises(ValueError):
        evaluate_sync(
            "findeepindicator",
            lambda case: calls.append(case) or "answer",
            allow_paid=True,
            judge_models=["mock/perfect"],
            extractor_model="mock/strong",
            data_path=path,
        )
    assert calls == []


@contextmanager
def failing_provider(status=503):
    attempts = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            attempts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = b'{"error":{"message":"offline transient failure","type":"server_error"}}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", attempts
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("provider", ["openai", "openrouter", "anthropic"])
@pytest.mark.parametrize("status", [429, 503])
def test_transient_real_provider_failure_is_one_attempt_and_grading_error(monkeypatch, tmp_path, provider, status):
    client = pytest.importorskip(
        "anthropic" if provider == "anthropic" else "openai",
        minversion="1.17.0" if provider == "anthropic" else "3.4.0",
    )
    if provider == "anthropic" and client.__version__.split(".")[0] != "1":
        pytest.skip("Current Inspect requires the finance extra's anthropic>=1.17,<2 client.")
    from inspect_ai.model import get_model

    path = pinned_data(monkeypatch, tmp_path, "findeepindicator")
    configured = []
    model_id = "claude-haiku-4-5-20251001" if provider == "anthropic" else "explicit-extractor"
    explicit_model = f"{provider}/{model_id}"
    with failing_provider(status) as (base_url, attempts):

        def local_model(model, **kwargs):
            configured.append((model, kwargs.copy()))
            evaluator = get_model(model, base_url=base_url, api_key="offline-test-key", **kwargs)
            assert evaluator.api.client.max_retries == 0  # Underlying SDK, not just Inspect.
            return evaluator

        monkeypatch.setattr(judging, "get_model", local_model)

        def agent(case):
            assert attempts == []  # Client preflight must never generate/probe.
            return "answer"

        report = evaluate_sync(
            "findeepindicator",
            agent,
            allow_paid=True,
            data_path=path,
            judge_models=[explicit_model],
            extractor_model=explicit_model,
            grading_timeout=5,
        )
    assert len(attempts) == 1 and attempts[0]["model"] == model_id
    assert report.results[0].status == "grading_error" and report.results[0].score is None
    assert configured[-1][0] == explicit_model
    assert configured[-1][1]["role"].startswith("everybench_evaluator_finance_extractor_")
    assert configured[-1][1]["max_retries"] == 0 and configured[-1][1]["memoize"] is False
    assert "offline transient failure" not in report.model_dump_json()


def test_ambient_role_cannot_replace_explicit_model_and_keeps_evaluator_attribution(monkeypatch, tmp_path):
    from inspect_ai import Task
    from inspect_ai import eval as inspect_eval
    from inspect_ai.dataset import Sample
    from inspect_ai.model import ChatMessageAssistant, ModelOutput, get_model
    from inspect_ai.scorer import Score, accuracy, scorer

    from everybench.inspect_convert import convert_sample

    roles = []
    original_get_model = get_model

    def explicit_mock(model, **kwargs):
        roles.append(kwargs["role"])
        assert model == "mockllm/explicit-evaluator"
        # The real Inspect factory must not resolve the ambient finance_extractor role.
        return original_get_model(
            model,
            custom_outputs=[
                ModelOutput(
                    model="explicit-evaluator",
                    choices=[{"message": ChatMessageAssistant(content='{"ok":true}'), "stop_reason": "stop"}],
                )
            ],
            **kwargs,
        )

    monkeypatch.setattr(judging, "get_model", explicit_mock)

    @scorer(metrics=[accuracy()])
    def auxiliary():
        async def score(state, target):
            assert await judging.ask_json("mockllm/explicit-evaluator", "finance_extractor", "Return JSON", {}) == {
                "ok": True
            }
            assert await judging.ask_json("mockllm/explicit-evaluator", "finance_extractor", "Return JSON", {}) == {
                "ok": True
            }
            return Score(value=1)

        return score

    ambient = original_get_model("mock/weak")
    task = Task(dataset=[Sample(id="original", input="What is 1+1?", target="2")], scorer=auxiliary())
    [log] = inspect_eval(
        task,
        model="mock/perfect",
        model_roles={"finance_extractor": ambient},
        log_dir=str(tmp_path / "logs"),
        display="none",
    )
    assert log.status == "success", log.error
    assert len(roles) == len(set(roles)) == 2
    events = [ev for ev in log.samples[0].events if ev.event == "model"]
    evaluator_events = [ev for ev in events if ev.role in roles]
    assert len(evaluator_events) == 2 and all(ev.output.model == "explicit-evaluator" for ev in evaluator_events)
    converted = convert_sample(log.samples[0], "run")
    assert "explicit-evaluator" not in converted["resolved_models"]
    assert "finance_extractor" not in roles and ambient.api.model_name == "weak"
    # The role map outside Inspect is untouched; no per-call diagnostic role leaks.
    from inspect_ai.model import model_roles

    assert not any(role in model_roles() for role in roles)


@pytest.mark.parametrize("failure", ["missing-sdk", "unsupported-provider", "unknown-mock", "incompatible-client"])
def test_finance_client_preflight_fails_before_any_agent_invocation(monkeypatch, tmp_path, failure):
    path = pinned_data(monkeypatch, tmp_path, "findeepindicator")
    calls = []
    model = "openai/explicit-model"
    if failure == "missing-sdk":
        monkeypatch.setattr("everybench.evaluation.benchmarks.importlib.util.find_spec", lambda name: None)
    elif failure == "unsupported-provider":
        model = "unsupported/explicit-model"
    elif failure == "unknown-mock":
        model = "mock/invented-alias"
    else:
        model = "anthropic/claude-haiku-4-5-20251001"
        monkeypatch.setattr("everybench.evaluation.benchmarks.importlib.util.find_spec", lambda name: object())
        monkeypatch.setattr("everybench.evaluation.benchmarks.version", lambda name: "2.3.0")
    with pytest.raises(ValueError):
        evaluate_sync(
            "findeepindicator",
            lambda case: calls.append(case) or "answer",
            allow_paid=True,
            data_path=path,
            judge_models=[model],
            extractor_model=model,
        )
    assert calls == []


def test_finance_descriptor_declares_single_judge_adaptation_and_no_official_parity():
    benchmark = FinanceBenchmark("bigfinance-public-50", judge_models=["mock/perfect"])
    assert benchmark.describe()["jury_mode"] == "single-judge-adaptation"
    assert benchmark.describe()["official_parity"] is False and benchmark.requires_trace
