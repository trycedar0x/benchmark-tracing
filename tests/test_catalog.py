"""Development fixtures stay runnable without appearing as product benchmarks."""

import json

import pytest
from typer.testing import CliRunner

from everybench.catalog import CatalogError, get_benchmark, load_catalog
from everybench.cli import app

INTERNAL = {"toy-arith@1", "toy-tools@1", "harbor-smoke@1"}


def test_public_catalog_excludes_internal_fixtures():
    public = load_catalog()
    all_entries = load_catalog(include_internal=True)
    assert INTERNAL.isdisjoint(public)
    assert set(all_entries) - set(public) == INTERNAL
    assert {"gsm8k@1", "terminal-bench@2", "tau3-retail@1"} <= public.keys()


@pytest.mark.parametrize("ref", sorted(INTERNAL))
def test_internal_fixtures_still_resolve_for_development(ref):
    entry = get_benchmark(ref)
    assert entry.internal
    assert get_benchmark(entry.id).ref == ref
    with pytest.raises(CatalogError):
        get_benchmark(ref, include_internal=False)
    # Visibility must not invalidate comparisons with existing runs.
    assert entry.variant_key == entry.model_copy(update={"internal": False}).variant_key


def test_cli_catalog_hides_fixtures_unless_explicitly_requested():
    runner = CliRunner()
    result = runner.invoke(app, ["catalog", "--json"])
    assert result.exit_code == 0, result.output
    assert INTERNAL.isdisjoint(e["ref"] for e in json.loads(result.output))
    text_result = runner.invoke(app, ["catalog"])
    assert text_result.exit_code == 0
    for ref in INTERNAL:
        assert ref not in text_result.output

    result = runner.invoke(app, ["catalog", "--json", "--include-internal"])
    assert result.exit_code == 0, result.output
    assert INTERNAL <= {e["ref"] for e in json.loads(result.output)}

    for ref in INTERNAL:
        assert runner.invoke(app, ["catalog", ref]).exit_code == 1
        detail = runner.invoke(app, ["catalog", ref, "--include-internal", "--json"])
        assert detail.exit_code == 0, detail.output
        assert json.loads(detail.output)["ref"] == ref


def test_offline_custom_benchmarks_are_not_hidden(tmp_path, monkeypatch):
    custom = tmp_path / "catalog.yaml"
    custom.write_text(
        "- id: local-benchmark\n  version: 1\n  family: Local\n"
        "  variant: Offline custom benchmark\n  task: local/task\n"
        "  grader: exact match\n  offline: true\n"
    )
    monkeypatch.setenv("EVERYBENCH_CATALOG", str(custom))
    load_catalog.cache_clear()
    try:
        assert "local-benchmark@1" in load_catalog()
    finally:
        load_catalog.cache_clear()
