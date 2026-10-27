"""CLI catalog is static metadata for implemented user-owned finance adapters only."""

import json

import pytest
from typer.testing import CliRunner

from everybench.cli import app


def test_catalog_lists_only_implemented_adapters_without_loading_data(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Catalog must not load cases or instantiate graders")

    monkeypatch.setattr("everybench.finance.datasets.load_dataset", forbidden)
    result = CliRunner().invoke(app, ["catalog", "--json"])
    assert result.exit_code == 0, result.output
    entries = {e["id"]: e for e in json.loads(result.output)}
    assert set(entries) == {"findeepindicator", "bigfinance-public-50"}
    assert entries["findeepindicator"]["count"] == 3350
    assert entries["bigfinance-public-50"]["count"] == 50
    assert entries["bigfinance-public-50"]["requires_trace"]
    assert not entries["findeepindicator"]["requires_trace"]
    assert all(e["experimental"] and not e["official_parity"] and e["license_url"] for e in entries.values())


def test_catalog_detail_and_unknown():
    runner = CliRunner()
    result = runner.invoke(app, ["catalog", "findeepindicator", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["id"] == "findeepindicator"
    assert runner.invoke(app, ["catalog", "toy-arith"]).exit_code == 1


def test_help_has_no_legacy_execution_commands():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "evaluate" in result.output and "catalog" in result.output
    for command in ("run", "quote"):
        rejected = runner.invoke(app, [command, "--help"])
        assert rejected.exit_code != 0
        assert "No such command" in rejected.output


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "example.com"])
def test_serve_rejects_unauthenticated_non_loopback_before_backend_import(host):
    result = CliRunner().invoke(app, ["serve", "--host", host])
    assert result.exit_code == 1
    assert "requires EVERYBENCH_AUTH=1" in result.output
