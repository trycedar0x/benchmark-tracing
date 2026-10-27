"""Publishing is deterministic and does not need EveryBench runtime or network access."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("everybench_docs_builder", ROOT / "scripts/build_docs.py")
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


def test_committed_docs_match_canonical_sources():
    assert builder.build(check=True) == []


def test_navigation_pages_exist_and_only_allowlisted_sources_are_published():
    config = json.loads((ROOT / "docs-site/docs.json").read_text())
    routes = [page for group in config["navigation"]["groups"] for page in group["pages"]]
    assert routes[0] == "index"
    assert "guides/coding-agents" in routes[:3]
    for route in routes:
        assert (ROOT / "docs-site" / f"{route}.mdx").is_file()
    assert set(builder.ROUTES.values()) == {"/" + route for route in routes if route.startswith("guides/")}
    assert not any("plan" in page.source or "acceptance" in page.source for page in builder.PAGES)


def test_links_are_local_or_existing_source_without_changing_literal_code(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/agents.md").write_text("# Agents\n")
    (tmp_path / "example.py").write_text("print('offline')\n")
    original = (
        "[Agents](agents.md#tracing) and [code](../example.py).\n"
        "Version <4; literal {value}.\n"
        "`[literal](missing.md)`\n"
        "```python\nprint('[literal](missing.md)')\n```\n"
    )
    actual = builder.rewrite_links(original, "docs/guide.md", tmp_path)
    assert "[Agents](/guides/coding-agents#tracing)" in actual
    assert builder.SOURCE_URL + "example.py" in actual
    assert "Version &lt;4; literal &#123;value&#125;." in actual
    assert "`[literal](missing.md)`" in actual
    assert "print('[literal](missing.md)')" in actual
    assert builder.rewrite_links("[external](https://example.com) [self](#section)", "docs/guide.md", tmp_path)
    with pytest.raises(ValueError, match="missing source link"):
        builder.rewrite_links("[broken](missing.md)", "docs/guide.md", tmp_path)


def test_generated_frontmatter_replaces_skill_metadata_and_duplicate_heading(tmp_path):
    source = tmp_path / "skill.md"
    source.write_text("---\nname: consumer\ndescription: original\n---\n\n# Original heading\n\nReal instructions.\n")
    page = builder.Page("skill.md", "consumer", "Consumer skill", "Public description.")
    result = builder.render(page, tmp_path)
    assert 'title: "Consumer skill"' in result
    assert "name: consumer" not in result
    assert "# Original heading" not in result
    assert "Real instructions." in result
    assert "Generated from skill.md" in result


def test_check_detects_drift_and_generation_only_removes_owned_pages(tmp_path, monkeypatch):
    page = builder.Page("guide.md", "test", "Test", "Test guide.")
    monkeypatch.setattr(builder, "PAGES", (page,))
    (tmp_path / "guide.md").write_text("# Test\n\nOffline content.\n")
    site = tmp_path / "docs-site"
    site.mkdir()
    manual = site / "index.mdx"
    manual.write_text("Handwritten landing page.\n")
    assert builder.build(tmp_path, check=True) == ["docs-site/guides/test.mdx"]
    assert not (site / "guides").exists()
    builder.build(tmp_path)
    assert builder.build(tmp_path, check=True) == []
    stale = site / "guides/removed.mdx"
    stale.write_text("Obsolete generated page.\n")
    (tmp_path / "guide.md").write_text("# Test\n\nUpdated content.\n")
    assert len(builder.build(tmp_path, check=True)) == 2
    builder.build(tmp_path)
    assert not stale.exists()
    assert manual.read_text() == "Handwritten landing page.\n"
    assert builder.build(tmp_path, check=True) == []
