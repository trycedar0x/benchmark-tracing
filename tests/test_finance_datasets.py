import hashlib
import io
import json
from dataclasses import replace

import pytest

from everybench.evaluation.benchmarks import FinanceBenchmark
from everybench.finance.datasets import DATASETS, DatasetError, load_dataset, parse_dataset

BIG_ROW = {
    "id": "example-1",
    "query": "Research a company's adjusted earnings.",
    "reference_answer": "protected reference",
    "rubric": [{"text": "protected criterion", "points": 2}],
    "evaluation_only": True,
    "do_not_train": True,
    "benchmark_canary": "protected canary",
}
INDICATOR_ROW = {
    "id": "0",
    "question": "Calculate the financial ratio.",
    "answer": "12.5",
    "formula": "profit/revenue",
    "data": "year,profit,revenue\n2024,1,8\n",
    "data_indicator": "",
    "market": "US",
}


def spec_for(body, name="bigfinance-public-50"):
    return replace(DATASETS[name], sha256=hashlib.sha256(body).hexdigest(), count=1, revision="test")


def test_jsonl_schema_and_evaluation_restrictions_are_preserved():
    body = json.dumps(BIG_ROW).encode()
    rows = parse_dataset(body, spec_for(body))
    assert rows[0]["do_not_train"] and rows[0]["benchmark_canary"] == "protected canary"
    assert rows[0]["reference_answer"] == BIG_ROW["reference_answer"]
    assert rows[0]["rubric"] == BIG_ROW["rubric"]


@pytest.mark.parametrize("name,row", [("bigfinance-public-50", BIG_ROW), ("findeepindicator", INDICATOR_ROW)])
def test_reference_fields_never_appear_in_agent_input(monkeypatch, name, row):
    monkeypatch.setattr("everybench.evaluation.benchmarks.load_dataset", lambda name: [row])
    adapter = FinanceBenchmark(
        name, judge_models=["mock/perfect"], extractor_model="mock/perfect" if name == "findeepindicator" else None
    )
    [case] = adapter.cases()
    assert case.input == row["query" if name == "bigfinance-public-50" else "question"]
    assert "protected" not in case.input and "profit/revenue" not in case.input
    assert case.reference == row


@pytest.mark.parametrize("change", ["hash", "count", "duplicate", "rubric", "missing", "id"])
def test_bad_datasets_fail_closed(change):
    row = json.loads(json.dumps(BIG_ROW))
    if change == "rubric":
        row["rubric"][0]["points"] = -1
    elif change == "missing":
        del row["query"]
    elif change == "id":
        row["id"] = None
    body = json.dumps(row).encode()
    if change == "duplicate":
        body += b"\n" + body
    spec = spec_for(body)
    if change == "hash":
        spec = replace(spec, sha256="0" * 64)
    elif change == "count":
        spec = replace(spec, count=2)
    elif change == "duplicate":
        spec = replace(spec, count=2)
    with pytest.raises(DatasetError):
        parse_dataset(body, spec)


def test_cached_data_is_reverified_and_does_not_refetch(monkeypatch, tmp_path):
    body = json.dumps(BIG_ROW).encode()
    spec = spec_for(body)
    monkeypatch.setitem(DATASETS, spec.name, spec)
    calls = []

    def fetch(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(body)

    monkeypatch.setattr("urllib.request.urlopen", fetch)
    assert len(load_dataset(spec.name, cache_dir=tmp_path)) == 1
    assert len(load_dataset(spec.name, cache_dir=tmp_path)) == 1
    assert len(calls) == 1
    directory = tmp_path / spec.name / spec.revision
    assert json.loads((directory / "source.json").read_text())["license"] == "CC-BY-4.0"
    (directory / "data.jsonl").write_bytes(body + b"changed")
    with pytest.raises(DatasetError, match="mismatch"):
        load_dataset(spec.name, cache_dir=tmp_path)
    assert len(calls) == 1
