"""Publish an explicit allowlist of existing consumer guides as Mintlify pages.

No EveryBench imports, database access, provider calls or credentials are needed.
Canonical Markdown and portable skills remain the source of truth.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://github.com/trycedar0x/benchmark-tracing/blob/main/"


@dataclass(frozen=True)
class Page:
    source: str
    slug: str
    title: str
    description: str


PAGES = (
    Page(
        "docs/agents.md",
        "coding-agents",
        "Use with your coding agent",
        "Let your coding agent integrate and operate EveryBench in your own project.",
    ),
    Page(
        "skills/README.md",
        "install-skills",
        "Install portable skills",
        "Install self-contained evaluation and tracing skills in your coding agent's project.",
    ),
    Page(
        "docs/guide.md",
        "guide",
        "Getting started",
        "Choose the SDK, CLI or queued API, verify offline, and read results safely.",
    ),
    Page(
        "docs/cli.md",
        "cli",
        "Using the CLI",
        "CLI setup, real evaluation, structured output, command-specific exit codes and safe automation.",
    ),
    Page(
        "docs/evaluating-your-agent.md",
        "evaluate",
        "Evaluate your existing agent",
        "Callable and strict HTTP contracts, grading, trace sources, reports and queued submissions.",
    ),
    Page(
        "skills/everybench-sdk/references/evaluation.md",
        "evaluation-sdk",
        "Evaluation SDK",
        "Python evaluation types, callables, HTTP targets and independent trace sources.",
    ),
    Page(
        "skills/everybench-sdk/references/python-sdk.md",
        "tracing-sdk",
        "Tracing SDK",
        "Instrument actual operations, apply privacy policies and verify durable delivery.",
    ),
    Page(
        "skills/everybench/references/http-api.md",
        "http-api",
        "HTTP API",
        "Submit authorized target IDs and inspect workspace-scoped results and traces.",
    ),
    Page(
        "docs/backend-evaluation-contract.md",
        "operators",
        "Configure targets and workers",
        "Operator target profiles, independent credentials, authentication and queue lifecycle.",
    ),
    Page(
        "docs/typescript-tracing.md",
        "typescript-tracing",
        "TypeScript and OpenTelemetry",
        "Keep your Node.js OTel instrumentation and export real traces to EveryBench.",
    ),
    Page(
        "docs/finance-evaluations.md",
        "finance",
        "Finance benchmarks",
        "FinDeepIndicator and BigFinance public-50 requirements, scoring, licenses and limitations.",
    ),
    Page(
        "examples/README.md",
        "examples",
        "Examples",
        "Offline callable and tracing fixtures, plus optional framework integrations.",
    ),
    Page(
        "skills/everybench/SKILL.md",
        "evaluation-skill",
        "Evaluation skill instructions",
        "The portable workflow for evaluating an existing agent and comparing persisted runs.",
    ),
    Page(
        "skills/everybench-sdk/SKILL.md",
        "tracing-skill",
        "SDK skill instructions",
        "The portable workflow for SDK evaluation, real instrumentation and delivery checks.",
    ),
)
ROUTES = {page.source: f"/guides/{page.slug}" for page in PAGES}
LINK = re.compile(r"(!?\[[^\]\n]*\]\()([^\s)]+)(\))")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def rewrite_links(body: str, source: str, root: Path) -> str:
    """Keep published guide links local; send code/history links to their source."""
    fence = ""
    lines = []

    def replace(match: re.Match[str]) -> str:
        target = match[2]
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path or target.startswith("/"):
            return match[0]
        path = posixpath.normpath(posixpath.join(posixpath.dirname(source), url.path))
        if path.startswith("../") or not (root / path).is_file():
            raise ValueError(f"{source}: missing source link {target!r}")
        if path.startswith("docs-site/reference/") and Path(path).suffix in (".mdx", ".json"):
            destination = "/" + path.removeprefix("docs-site/").removesuffix(".mdx")
        else:
            destination = ROUTES.get(path, SOURCE_URL + quote(path, safe="/"))
        if url.query:
            destination += "?" + url.query
        if url.fragment:
            destination += "#" + url.fragment
        return match[1] + destination + match[3]

    for line in body.splitlines(keepends=True):
        marker = FENCE.match(line)
        if marker:
            token = marker[1]
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = ""
            lines.append(line)
        elif fence:
            lines.append(line)
        else:
            # Inline code is literal, just like fenced executable examples.
            parts = re.split(r"(`+[^`\n]*`+)", line)
            lines.append(
                "".join(
                    part
                    if part.startswith("`")
                    else LINK.sub(replace, part).replace("<", "&lt;").replace("{", "&#123;").replace("}", "&#125;")
                    for part in parts
                )
            )
    return "".join(lines)


def render(page: Page, root: Path = ROOT) -> str:
    body = (root / page.source).read_text()
    body = re.sub(r"\A---\n.*?\n---\n", "", body, count=1, flags=re.DOTALL).lstrip()
    body = re.sub(r"\A# [^\n]+\n", "", body, count=1).lstrip()
    body = rewrite_links(body, page.source, root)
    return (
        "---\n"
        f"title: {json.dumps(page.title)}\n"
        f"description: {json.dumps(page.description)}\n"
        "---\n\n"
        f"{{/* Generated from {page.source}; edit the source, then run scripts/build_docs.py. */}}\n\n"
        + body.rstrip()
        + "\n"
    )


def build(root: Path = ROOT, *, check: bool = False) -> list[str]:
    directory = root / "docs-site" / "guides"
    expected = {directory / f"{page.slug}.mdx": render(page, root) for page in PAGES}
    stale = sorted(set(directory.glob("*.mdx")) - set(expected))
    changed = [path for path, content in expected.items() if not path.exists() or path.read_text() != content]
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
        for path in changed:
            path.write_text(expected[path])
        for path in stale:
            path.unlink()
    return [str(path.relative_to(root)) for path in changed + stale]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if committed pages differ from their sources.")
    args = parser.parse_args()
    changes = build(check=args.check)
    if args.check and changes:
        parser.exit(1, "Docs are stale; run python scripts/build_docs.py:\n" + "\n".join(changes) + "\n")
    print(f"{'Checked' if args.check else 'Generated'} {len(PAGES)} Mintlify guides.")


if __name__ == "__main__":
    main()
