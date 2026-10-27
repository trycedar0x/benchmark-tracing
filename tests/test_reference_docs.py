"""Public references follow code, without invoking agents, workers or databases."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("everybench_reference_builder", ROOT / "scripts/build_references.py")
references = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = references
SPEC.loader.exec_module(references)


@pytest.fixture(scope="module")
def rendered():
    return references.render_files()


def test_committed_references_match_code(rendered):
    for name, content in rendered.items():
        assert (ROOT / "docs-site/reference" / name).read_text() == content, name


def test_public_sdk_exports_methods_types_and_defaults_are_documented(rendered):
    from everybench import evaluation, sdk

    combined = "\n".join(rendered[name] for name in ("sdk-evaluation.mdx", "sdk-tracing.mdx", "sdk-types.mdx"))
    for name in evaluation.__all__:
        if hasattr(sdk, name):
            assert f"## {name}\n" in combined, name
    for name in ("trace", "invocation", "span", "record", "health", "flush", "close"):
        assert f"### EveryBench.{name}" in rendered["sdk-tracing.mdx"]
    assert "spool_dir" in combined and "268435456" in combined
    assert "allow_paid" in combined and "False" in combined
    schemas = json.loads(rendered["sdk-models.json"])["$defs"]
    assert schemas["AgentInput"]["additionalProperties"] is False
    assert "reference" not in schemas["AgentInput"]["properties"]
    assert schemas["GradeResult"]["properties"]["score"]["maximum"] == 1
    assert schemas["TraceEvidence"]["properties"]["state"]["enum"] == [
        "pending",
        "complete",
        "incomplete",
        "missing",
        "withheld",
    ]
    assert "EveryBench.evaluate(" not in rendered["sdk-tracing.mdx"]


def test_cli_reference_covers_actual_tree_and_only_calls_help(monkeypatch, rendered):
    root, commands = references.cli_commands()

    def forbidden(*args, **kwargs):
        pytest.fail("A CLI business callback was invoked while documenting --help")

    for _, command in commands:
        monkeypatch.setattr(command, "callback", forbidden)
    monkeypatch.setattr(references, "cli_commands", lambda: (root, commands))
    actual = references.cli_file()
    assert actual == rendered["cli.mdx"]
    for path, _ in commands:
        name = "everybench" + (" " + " ".join(path) if path else "")
        assert f"## {name}\n" in actual
    assert "## everybench admin set-secret\n" in actual
    assert "## everybench import inspect-log\n" in actual
    assert "--trace-token-env" in actual and "--extractor" in actual
    assert "## everybench quote\n" not in actual and "## everybench run\n" not in actual
    assert "\x1b[" not in actual


def test_cli_reference_is_independent_of_terminal_or_ci_presentation(monkeypatch, rendered):
    from typer import rich_utils

    monkeypatch.setattr(rich_utils, "MAX_WIDTH", 40)
    monkeypatch.setattr(rich_utils, "COLOR_SYSTEM", "standard")
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", True)
    assert references.cli_file() == rendered["cli.mdx"]
    assert rich_utils.MAX_WIDTH == 40 and rich_utils.FORCE_TERMINAL is True


def test_http_schema_is_actual_openapi_without_starting_lifespan(rendered):
    from everybench.server import create_app

    schema = json.loads(rendered["openapi.json"])
    assert schema == create_app(workers=0).openapi()
    assert "post" in schema["paths"]["/api/runs"]
    assert "/v1/traces" in schema["paths"]
    assert "/api/quotes" not in schema["paths"]
    for path, operations in schema["paths"].items():
        for method in operations:
            assert f"## {method.upper()} {references.prose(path)}\n" in rendered["http.mdx"]


def test_generation_does_not_access_runtime_capabilities(monkeypatch, tmp_path):
    import httpx

    from everybench import db, sdk, server
    from everybench.evaluation import engine

    def forbidden(*args, **kwargs):
        pytest.fail("Reference generation accessed a runtime capability")

    for module, name in ((db, "get_engine"), (server, "get_engine"), (engine, "evaluate")):
        monkeypatch.setattr(module, name, forbidden)
    for cls in (sdk.EveryBench, sdk.HttpAgent, sdk.FinanceBenchmark, sdk.HTTPTraceSource):
        monkeypatch.setattr(cls, "__init__", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    home = tmp_path / "unused-home"
    canary = "docs_generation_must_not_capture_runtime_credentials"
    monkeypatch.setenv("EVERYBENCH_HOME", str(home))
    monkeypatch.setenv("EVERYBENCH_API_KEY", canary)
    monkeypatch.setenv("OPENAI_API_KEY", canary)
    output = references.render_files()
    assert canary not in "\n".join(output.values())
    assert not home.exists()


def test_reference_check_detects_drift_and_retired_owned_outputs(monkeypatch, tmp_path):
    monkeypatch.setattr(references, "ROOT", tmp_path)
    monkeypatch.setattr(references, "render_files", lambda: {"cli.mdx": "generated help\n", "openapi.json": "{}\n"})
    assert len(references.build(check=True)) == 2
    assert not (tmp_path / "docs-site/reference").exists()
    references.build()
    assert references.build(check=True) == []
    stale = tmp_path / "docs-site/reference/old.json"
    stale.write_text("obsolete\n")
    current = tmp_path / "docs-site/reference/cli.mdx"
    current.write_text("stale reference\n")
    assert len(references.build(check=True)) == 2
    references.build()
    assert not stale.exists()
    assert current.read_text() == "generated help\n"
