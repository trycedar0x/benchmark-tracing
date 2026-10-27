"""everybench command-line interface."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree
from sqlalchemy import select

from everybench.compare import compare_runs
from everybench.config import env
from everybench.db import Run, SampleResult, Span, Trace, session_scope
from everybench.trace_diff import content_field

app = typer.Typer(
    help="Evaluate your own agents, inspect traces, and compare runs.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()
err = Console(stderr=True)


def _who(model: str | None, agent: str | None, target: dict | None = None) -> str:
    """Render actual external target identity, not a fictitious model."""
    if model is None:
        target = target or {}
        return f"{target.get('name', 'External agent')} ({target.get('version') or 'unversioned'})"
    return f"{model} · {agent.rsplit(':', 1)[-1]}" if agent else model


def _fail(message: str, code: int = 1) -> None:
    err.print(f"[red]Error:[/red] {escape(message)}")
    raise typer.Exit(code)


def _money(value: float | None) -> str:
    if value is None:
        return "–"
    return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.1f}%"


def _accuracy(run: Run) -> float | None:
    scored = run.samples_done - run.n_error
    return run.n_correct / scored if scored else None


def _primary_metric(run: Run) -> tuple[str, float | None]:
    if run.target_kind == "external_agent":
        return "Score", (run.metrics or {}).get("mean_score")
    return "Accuracy", _accuracy(run)


# ---------------------------------------------------------------------------- catalog


@app.command()
def catalog(
    benchmark: Annotated[str | None, typer.Argument(help="Show details for one benchmark.")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List implemented experimental user-agent adapters; never load datasets or providers."""
    from dataclasses import asdict

    from everybench.finance.datasets import DATASETS

    entries = [
        asdict(spec)
        | {
            "id": spec.name,
            "experimental": True,
            "official_parity": False,
            "requires_trace": spec.name == "bigfinance-public-50",
            "grading_requirements": (
                "Explicit judge models and complete visible agent trace evidence."
                if spec.name == "bigfinance-public-50"
                else "One explicit judge and an explicit extractor; grades the submitted response."
            ),
        }
        for spec in DATASETS.values()
    ]
    if benchmark:
        entries = [entry for entry in entries if entry["id"] == benchmark]
        if not entries:
            _fail(f"Unknown user-agent benchmark {benchmark!r}.")
    if as_json:
        print(json.dumps(entries[0] if benchmark else entries))
        return
    table = Table(title="Experimental user-agent benchmarks (not official leaderboard parity)")
    for column in ("Benchmark", "Cases", "License", "Grading requirements"):
        table.add_column(column, overflow="fold")
    for entry in entries:
        table.add_row(entry["id"], str(entry["count"]), entry["license"], entry["grading_requirements"])
    console.print(table)


@app.command()
def cancel(run_id: str) -> None:
    """Cancel a queued or running run."""
    from everybench.execution import request_cancel

    try:
        run_ = request_cancel(run_id)
    except LookupError as ex:
        _fail(str(ex))
    console.print(f"{run_.id}: {run_.status}")


# ---------------------------------------------------------------------------- inspect results


@app.command("runs")
def list_runs(
    limit: Annotated[int, typer.Option(help="How many to show.")] = 20,
    include_quotes: Annotated[bool, typer.Option(help="Include historical quote sample runs.")] = False,
) -> None:
    """List recent runs."""
    with session_scope() as session:
        rows = session.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit * 3)).all()
        rows = [r for r in rows if include_quotes or (r.manifest or {}).get("purpose") != "quote"][:limit]
        table = Table(title="Recent runs")
        metric_column = "Accuracy"
        if any(r.target_kind == "external_agent" for r in rows):
            metric_column = "Score" if all(r.target_kind == "external_agent" for r in rows) else "Score / Accuracy"
        for col in ("Run", "Created", "Benchmark", "Model", "Status", "Samples", metric_column, "Errors", "Cost"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                r.id,
                r.created_at.strftime("%Y-%m-%d %H:%M"),
                r.benchmark,
                _who(r.model, r.agent, r.target),
                r.status,
                f"{r.samples_done}/{r.samples_total or '?'}",
                _pct(_primary_metric(r)[1]),
                str(r.n_error),
                _money(r.cost_usd),
            )
    console.print(table)


@app.command()
def show(
    run_id: str,
    outcome: Annotated[str | None, typer.Option(help="Filter samples: correct, incorrect, error, ...")] = None,
    samples: Annotated[int, typer.Option(help="Samples to list.")] = 20,
) -> None:
    """Show a run's summary, manifest and samples."""
    with session_scope() as session:
        r = session.get(Run, run_id)
        if r is None:
            _fail(f"Run {run_id} not found")
        query = select(SampleResult).where(SampleResult.run_id == run_id)
        if outcome:
            query = query.where(SampleResult.outcome == outcome)
        rows = session.scalars(query.order_by(SampleResult.sample_id, SampleResult.epoch).limit(samples)).all()
        summary = Table(show_header=False, box=None)
        versions = (r.manifest or {}).get("versions", {})
        for key, value in [
            ("run", r.id),
            ("benchmark", r.benchmark),
            ("variant", r.variant_key),
            ("target", _who(r.model, r.agent, r.target)),
            ("agent", r.agent or "–"),
            ("resolved models", ", ".join(r.resolved_models or []) or "–"),
            ("status", r.status),
            ("samples", f"{r.samples_done}/{r.samples_total or '?'}"),
            (_primary_metric(r)[0].lower(), _pct(_primary_metric(r)[1])),
            *([("fully-correct accuracy", _pct(_accuracy(r)))] if r.target_kind == "external_agent" else []),
            ("errors", r.n_error),
            ("tokens", f"{r.input_tokens:,} in / {r.output_tokens:,} out"),
            ("estimated cost", _money(r.cost_usd)),
            ("budget", _money(r.budget_usd)),
            ("content policy", r.content_policy),
            ("metrics", json.dumps(r.metrics)),
            ("versions", ", ".join(f"{k} {v}" for k, v in versions.items())),
            ("log", r.log_path or "–"),
            ("error", (r.error or "").strip()[:300] or "–"),
        ]:
            summary.add_row(f"[dim]{key}[/dim]", escape(str(value)))
        table = Table(title="Samples")
        for col in ("Sample", "Epoch", "Outcome", "Score", "Answer", "Target", "Tokens", "Trace"):
            table.add_column(col, overflow="fold")
        for s in rows:
            table.add_row(
                s.sample_id,
                str(s.epoch),
                s.outcome,
                "–" if s.score is None else f"{s.score:g}",
                escape((s.answer or s.error or "")[:60]),
                escape((s.target or "")[:30]),
                f"{s.input_tokens + s.output_tokens:,}",
                (s.trace_id or "")[:12],
            )
    console.print(summary)
    console.print(table)


def _duration(start: datetime | None, end: datetime | None) -> str:
    if not start or not end:
        return ""
    ms = (end - start).total_seconds() * 1000
    return f"{ms:.0f}ms" if ms < 1000 else f"{ms / 1000:.2f}s"


def _span_label(span: Span) -> str:
    color = {"model": "cyan", "tool": "magenta", "scorer": "green", "error": "red", "sandbox": "yellow"}.get(
        span.kind, "white"
    )
    bits = [
        f"[{color}]{span.kind}[/{color}] {escape(span.name)}",
        f"[dim]{_duration(span.start_time, span.end_time)}[/dim]",
    ]
    attrs = span.attributes or {}
    if "gen_ai.usage.input_tokens" in attrs:
        bits.append(
            f"[dim]{attrs.get('gen_ai.usage.input_tokens')}→{attrs.get('gen_ai.usage.output_tokens')} tok[/dim]"
        )
    if "score.value" in attrs:
        bits.append(f"score={attrs['score.value']}")
    if span.status == "error":
        bits.append("[red]error[/red]")
    previews = (content_field(span.content, name) for name in ("output", "result", "answer", "message"))
    preview = next((p for p in previews if isinstance(p, str)), None)
    if isinstance(preview, str) and preview:
        bits.append(f"[dim]“{escape(preview[:70])}”[/dim]")
    if span.content_state in ("withheld", "redacted"):
        bits.append(f"[dim]content {span.content_state}[/dim]")
    return " ".join(bits)


def _build_tree(spans: list[Span], title: str) -> Tree:
    children: dict[str | None, list[Span]] = defaultdict(list)
    ids = {s.span_id for s in spans}
    for s in sorted(spans, key=lambda s: (s.start_time or datetime.min, s.id)):
        children[s.parent_id if s.parent_id in ids else None].append(s)
    tree = Tree(title)

    def add(node: Tree, parent: str | None) -> None:
        for s in children.get(parent, []):
            add(node.add(_span_label(s)), s.span_id)

    add(tree, None)
    return tree


@app.command()
def trace(
    run_id: Annotated[str, typer.Argument(help="Run id, or a trace id with --trace-id.")],
    sample_id: Annotated[str | None, typer.Argument(help="Sample id within the run.")] = None,
    epoch: Annotated[int, typer.Option(help="Epoch.")] = 1,
    trace_id: Annotated[bool, typer.Option("--trace-id", help="Treat the first argument as a trace id.")] = False,
) -> None:
    """Print the span tree for one sample (or any trace)."""
    with session_scope() as session:
        if trace_id:
            tid = run_id
        else:
            if sample_id is None:
                _fail("Give a sample id, e.g. `everybench trace RUN arith-003`.")
            sample = session.scalars(
                select(SampleResult).where(
                    SampleResult.run_id == run_id, SampleResult.sample_id == sample_id, SampleResult.epoch == epoch
                )
            ).first()
            if sample is None:
                _fail(f"Sample {sample_id} (epoch {epoch}) not found in {run_id}")
            tid = sample.trace_id
        header = session.get(Trace, tid)
        spans = session.scalars(select(Span).where(Span.trace_id == tid)).all()
        if not spans:
            _fail(f"No spans for trace {tid}")
        title = f"trace {tid}" + (f" · {header.source}" if header else "")
        console.print(_build_tree(list(spans), title))


@app.command()
def diff(
    run_a: str,
    run_b: str,
    sample_id: str,
    epoch: Annotated[int, typer.Option(help="Epoch.")] = 1,
) -> None:
    """Diff the traces of one sample across two runs: where did they diverge?"""
    from everybench.trace_diff import diff_traces

    with session_scope() as session:
        ids = []
        for run_id in (run_a, run_b):
            sample = session.scalars(
                select(SampleResult).where(
                    SampleResult.run_id == run_id, SampleResult.sample_id == sample_id, SampleResult.epoch == epoch
                )
            ).first()
            if sample is None or not sample.trace_id:
                _fail(f"Sample {sample_id} (epoch {epoch}) not found in {run_id}")
            ids.append(sample.trace_id)
        result = diff_traces(session, ids[0], ids[1])
    console.print(
        f"A: {result.a['outcome']} (score {result.a['score']})   B: {result.b['outcome']} (score {result.b['score']})"
    )
    console.print(f"[bold]{escape(result.summary)}[/bold]")
    table = Table()
    for col in ("#", "Step", "A", "B", "Differs"):
        table.add_column(col, overflow="fold")
    for i, p in enumerate(result.pairs, 1):
        step = p.a or p.b
        color = {"same": "dim", "changed": "yellow", "only_a": "red", "only_b": "green"}[p.op]
        marker = "→ " if result.first_divergence == i - 1 else ""
        table.add_row(
            f"{marker}{i}",
            f"[{color}]{step.kind} {escape(step.name)}[/{color}]",
            escape(p.a.summary) if p.a else "–",
            escape(p.b.summary) if p.b else "–",
            ", ".join(p.differences) or ("" if p.op == "same" else p.op),
        )
    console.print(table)


@app.command()
def compare(
    run_a: str,
    run_b: str,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    force: Annotated[bool, typer.Option(help="Compare even when variants are incompatible.")] = False,
    show_rows: Annotated[int, typer.Option(help="Changed samples to list.")] = 15,
) -> None:
    """Paired comparison of two runs: per-task changes and significance."""
    try:
        result = compare_runs(run_a, run_b, force=force)
    except LookupError as ex:
        _fail(str(ex))
    if as_json:
        print(json.dumps(result.to_dict(), default=str))
        return
    c = result.compatibility
    for msg in c.blocking:
        console.print(f"[red]Blocked:[/red] {escape(msg)}")
    for msg in c.warnings:
        console.print(f"[yellow]Warning:[/yellow] {escape(msg)}")
    if not c.comparable and not force:
        console.print("Not comparing. Pass --force for an exploratory comparison.")
        raise typer.Exit(2)
    a, b = result.run_a, result.run_b
    summary = Table(
        title=(
            f"{a['benchmark']}: A={_who(a['model'], a.get('agent'), a.get('target'))} "
            f"vs B={_who(b['model'], b.get('agent'), b.get('target'))}"
        )
    )
    for col in ("", "A", "B", "Δ (B−A)", "95% CI", "McNemar p"):
        summary.add_column(col)
    ci = result.delta_ci95
    summary.add_row(
        f"Score ({result.n_scored_pairs} paired)",
        _pct(result.mean_a),
        _pct(result.mean_b),
        "–" if result.delta is None else f"{result.delta * 100:+.1f} pts",
        f"{ci[0] * 100:+.1f} to {ci[1] * 100:+.1f}" if ci else "–",
        "–" if result.mcnemar_p is None else f"{result.mcnemar_p:.3g}",
    )
    console.print(summary)
    d = result.discordant
    console.print(
        f"Improvements: {d.get('improvements', '–')}   Regressions: {d.get('regressions', '–')}   "
        f"Errors: A only {result.errors.get('a_only', 0)}, B only {result.errors.get('b_only', 0)}, "
        f"both {result.errors.get('both', 0)} (excluded from scores)"
    )
    changed = [r for r in result.rows if r.change in ("regression", "improvement", "error")]
    if changed:
        table = Table(title="Changed samples")
        for col in ("Sample", "Change", "A", "B", "Trace A", "Trace B"):
            table.add_column(col)
        for r in changed[:show_rows]:
            color = {"regression": "red", "improvement": "green"}.get(r.change, "yellow")
            table.add_row(
                r.sample_id,
                f"[{color}]{r.change}[/{color}]",
                r.a_outcome,
                r.b_outcome,
                (r.a_trace_id or "")[:12],
                (r.b_trace_id or "")[:12],
            )
        console.print(table)
        if len(changed) > show_rows:
            console.print(f"... {len(changed) - show_rows} more. Use --json for all rows.")
        first = changed[0]
        console.print(
            f"Inspect a change: [bold]everybench trace {a['id']} {first.sample_id}[/bold] and "
            f"[bold]everybench trace {b['id']} {first.sample_id}[/bold]"
        )


@app.command()
def export(
    run_id: str,
    fmt: Annotated[str, typer.Option("--format", help="json (everybench) or eee (Every Eval Ever).")] = "json",
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Output file (json) or directory (eee).")] = None,
) -> None:
    """Export a run's results."""
    with session_scope() as session:
        r = session.get(Run, run_id)
        if r is None:
            _fail(f"Run {run_id} not found")
        samples = session.scalars(select(SampleResult).where(SampleResult.run_id == run_id)).all()
        if fmt == "json":
            data: dict[str, Any] = {
                "schema": "everybench.results/v1",
                "run": {c.name: getattr(r, c.name) for c in Run.__table__.columns},
                "samples": [
                    {c.name: getattr(s, c.name) for c in SampleResult.__table__.columns if c.name != "id"}
                    for s in samples
                ],
            }
            text = json.dumps(data, default=str, indent=2)
            if out:
                out.write_text(text)
                console.print(f"Wrote {out}")
            else:
                print(text)
            return
        log_path = r.log_path
    if fmt != "eee":
        _fail("Format must be json or eee.")
    if not log_path:
        _fail("This run has no Inspect log to convert.")
    out = out or Path(f"{run_id}-eee")
    try:
        import every_eval_ever  # noqa: F401
    except ImportError:
        _fail("Every Eval Ever export needs the eee extra: uv sync --extra eee")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "every_eval_ever.converters.inspect",
            "--log_path",
            log_path,
            "--output_dir",
            str(out),
            "--source_organization_name",
            "everybench",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        _fail(f"Every Eval Ever conversion failed:\n{proc.stderr[-1500:]}")
    console.print(f"Wrote Every Eval Ever files to {out}/")


# ---------------------------------------------------------------------------- imports and datasets

import_app = typer.Typer(
    help="Import traces from Langfuse, LangSmith, Braintrust, OTLP files or Inspect logs.", no_args_is_help=True
)
app.add_typer(import_app, name="import")
RightsOpt = Annotated[str, typer.Option("--rights", help="Your right to use this data: own_data, licensed or unknown.")]
ContentOpt = Annotated[str, typer.Option("--content", help="Content policy: full, redacted or metadata.")]


def _run_import(source: str, project: str | None, rights: str, content: str, options: dict[str, Any]) -> None:
    from everybench.imports import create_import, execute_import

    try:
        record = create_import(source, project=project, usage_rights=rights, content_policy=content, options=options)
    except ValueError as ex:
        _fail(str(ex))
    with console.status(f"Importing from {source}..."):
        result = execute_import(record.id)
    if result.status != "succeeded":
        _fail(f"Import {result.id} failed: {result.error}")
    console.print(f"Imported {result.traces_imported} trace(s), {result.spans_imported} span(s) ({result.id}).")


def _api_import(source: str):
    def command(
        project: Annotated[str | None, typer.Option(help="Project name (or id for Braintrust).")] = None,
        max_traces: Annotated[int, typer.Option(help="Most recent traces to import.")] = 100,
        rights: RightsOpt = "unknown",
        content: ContentOpt = "redacted",
    ) -> None:
        _run_import(source, project, rights, content, {"max_traces": max_traces})

    label = {"langfuse": "Langfuse", "langsmith": "LangSmith", "braintrust": "Braintrust"}[source]
    command.__doc__ = f"Import traces from {label} (credentials from environment variables)."
    return command


for _source in ("langfuse", "langsmith", "braintrust"):
    import_app.command(_source)(_api_import(_source))


@import_app.command("otlp")
def import_otlp(path: Path, rights: RightsOpt = "unknown", content: ContentOpt = "redacted") -> None:
    """Import an OTLP/JSON file (one request, or one per line)."""
    _run_import("otlp_file", None, rights, content, {"path": str(path.resolve())})


@import_app.command("inspect-log")
def import_inspect_log(path: Path, rights: RightsOpt = "unknown", content: ContentOpt = "full") -> None:
    """Import an existing Inspect AI log as a run, with results and traces."""
    _run_import("inspect_log", None, rights, content, {"path": str(path.resolve())})


dataset_app = typer.Typer(help="Draft and review datasets from traces.", no_args_is_help=True)
app.add_typer(dataset_app, name="dataset")


@dataset_app.command("create")
def dataset_create(name: str, description: Annotated[str | None, typer.Option()] = None) -> None:
    """Create an empty dataset."""
    from everybench.datasets import create_dataset

    ds = create_dataset(name, description)
    console.print(f"Created {ds.id}")


@dataset_app.command("add")
def dataset_add(
    dataset_id: str,
    trace: Annotated[list[str] | None, typer.Option("--trace", help="Trace id; repeat for several.")] = None,
    run_id: Annotated[str | None, typer.Option("--run", help="Add samples from this run.")] = None,
    outcome: Annotated[list[str] | None, typer.Option(help="Only samples with this outcome, e.g. incorrect.")] = None,
    split: Annotated[str, typer.Option(help="unassigned, dev or test.")] = "unassigned",
) -> None:
    """Draft items from traces or from a run's samples."""
    from everybench.datasets import ReviewError, add_run_samples, add_traces

    try:
        items = add_traces(dataset_id, trace or [], split) if trace else []
        if run_id:
            items += add_run_samples(dataset_id, run_id, outcome or [], split)
    except (LookupError, ReviewError) as ex:
        _fail(str(ex))
    console.print(f"Added {len(items)} draft item(s). Review them before use: everybench dataset show {dataset_id}")


@dataset_app.command("show")
def dataset_show(dataset_id: str) -> None:
    """List a dataset's items and their review status."""
    from everybench.db import Dataset, DatasetItem

    with session_scope() as session:
        ds = session.get(Dataset, dataset_id)
        if ds is None:
            _fail(f"Dataset {dataset_id} not found")
        items = session.scalars(select(DatasetItem).where(DatasetItem.dataset_id == dataset_id)).all()
        table = Table(title=f"{ds.name} ({ds.id})")
        for col in ("Item", "Status", "Split", "Rights", "Input", "Historical output", "Reference"):
            table.add_column(col, overflow="fold")
        for i in items:
            table.add_row(
                str(i.id),
                i.status,
                i.split,
                i.usage_rights,
                escape((i.input or "–")[:50]),
                escape((i.historical_output or "–")[:40]),
                escape((i.reference_output or "–")[:40]),
            )
    console.print(table)


@dataset_app.command("review")
def dataset_review(
    item_id: int,
    reference: Annotated[str | None, typer.Option(help="Reference answer.")] = None,
    split: Annotated[str | None, typer.Option(help="dev or test.")] = None,
    rights: Annotated[str | None, typer.Option(help="own_data, licensed or unknown.")] = None,
    notes: Annotated[str | None, typer.Option()] = None,
    approve: Annotated[bool, typer.Option(help="Approve the item.")] = False,
    reject: Annotated[bool, typer.Option(help="Reject the item.")] = False,
    by: Annotated[str, typer.Option(help="Reviewer name.")] = "cli",
) -> None:
    """Set an item's reference answer, split and rights, and approve or reject it."""
    from everybench.datasets import ReviewError, review_item

    status = "approved" if approve else "rejected" if reject else None
    try:
        item = review_item(
            item_id,
            reviewer=by,
            reference_output=reference,
            split=split,
            usage_rights=rights,
            notes=notes,
            status=status,
        )
    except (LookupError, ReviewError) as ex:
        _fail(str(ex))
    console.print(f"Item {item.id}: {item.status}, split {item.split}, rights {item.usage_rights}")


@dataset_app.command("export")
def dataset_export(
    dataset_id: str,
    out: Annotated[Path, typer.Option("--out", "-o", help="JSONL file to write.")],
    split: Annotated[str | None, typer.Option(help="Only this split.")] = None,
) -> None:
    """Export approved items as Inspect-compatible JSONL (input, target, id, metadata)."""
    from everybench.datasets import export_items

    rows = export_items(dataset_id, split)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    console.print(f"Wrote {len(rows)} approved item(s) to {out}")


admin_app = typer.Typer(
    help="Manage users, workspaces, API keys and secrets (server with EVERYBENCH_AUTH=1).", no_args_is_help=True
)
app.add_typer(admin_app, name="admin")


@admin_app.command("create-user")
def admin_create_user(
    email: str,
    workspace: Annotated[str, typer.Option(help="Workspace name; created if missing.")],
    role: Annotated[str, typer.Option(help="owner, member or viewer.")] = "owner",
) -> None:
    """Create a user (or add an existing one) in a workspace. Prompts for a password."""
    from everybench.auth import AuthError, create_user

    password = typer.prompt("Password (10+ characters)", hide_input=True, confirmation_prompt=True)
    try:
        create_user(email, password, workspace, role)
    except AuthError as ex:
        _fail(str(ex))
    console.print(f"{email} is {role} of workspace {workspace}.")


@admin_app.command("create-key")
def admin_create_key(
    workspace: Annotated[str, typer.Option(help="Workspace name.")],
    name: Annotated[str, typer.Option(help="What the key is for.")],
    role: Annotated[str, typer.Option(help="owner, member or viewer.")] = "member",
) -> None:
    """Create a workspace API key. It is printed once."""
    from everybench.auth import AuthError, create_api_key, ensure_workspace

    with session_scope() as session:
        ws_id = ensure_workspace(session, workspace).id
    try:
        token = create_api_key(ws_id, name, role, created_by="admin-cli")
    except AuthError as ex:
        _fail(str(ex))
    print(token)
    err.print("Store this key now; it cannot be shown again.")


@admin_app.command("set-secret")
def admin_set_secret(
    name: str,
    workspace: Annotated[str | None, typer.Option(help="Workspace name; omit for local mode.")] = None,
) -> None:
    """Store an encrypted secret (e.g. OPENAI_API_KEY) for a workspace's runs and imports. Prompts for the value."""
    from everybench.auth import AuthError, ensure_workspace, set_secret

    value = typer.prompt(f"Value for {name}", hide_input=True)
    ws_id = None
    if workspace:
        with session_scope() as session:
            ws_id = ensure_workspace(session, workspace).id
    try:
        set_secret(ws_id, name, value)
    except AuthError as ex:
        _fail(str(ex))
    console.print(f"Stored {name}.")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8321,
    workers: Annotated[int, typer.Option(help="Embedded worker slots; 0 to use separate `everybench worker`.")] = 2,
) -> None:
    """Start the API server and web UI."""
    import logging

    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1") and env("AUTH") != "1":
        _fail(
            "Non-loopback serving requires EVERYBENCH_AUTH=1. "
            "Shared/reverse-proxied deployments require authentication."
        )

    from everybench.server import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    console.print(f"everybench on http://{host}:{port}  (workers: {workers})")
    uvicorn.run(create_app(workers=workers), host=host, port=port, log_level="warning")


@app.command()
def worker(concurrency: Annotated[int, typer.Option(help="Jobs to run at once.")] = 2) -> None:
    """Run queued agent evaluations and imports from the database."""
    import logging

    from everybench.jobs import Worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    w = Worker(concurrency=concurrency)
    console.print(f"Worker {w.name} with {concurrency} slot(s); Ctrl-C to stop.")
    w.run_forever()


@app.command()
def evaluate(
    benchmark: Annotated[str, typer.Argument(help="findeepindicator or bigfinance-public-50 (experimental).")],
    url: Annotated[str, typer.Option("--url", help="Explicit user-owned NDJSON chat endpoint.")],
    name: Annotated[str, typer.Option("--name", help="Actual agent identity.")],
    judge: Annotated[
        list[str],
        typer.Option(
            "--judge", help="Explicit openai/openrouter/anthropic or registered mock model; repeat for a jury."
        ),
    ],
    version: Annotated[str | None, typer.Option("--version")] = None,
    token_env: Annotated[str | None, typer.Option("--token-env")] = None,
    trace_url: Annotated[str | None, typer.Option("--trace-url")] = None,
    trace_token_env: Annotated[str | None, typer.Option("--trace-token-env")] = None,
    trace_store: Annotated[bool, typer.Option("--trace-store", help="Use the local EveryBench trace store.")] = False,
    workspace_id: Annotated[str | None, typer.Option("--workspace-id")] = None,
    extractor: Annotated[str | None, typer.Option("--extractor")] = None,
    data_path: Annotated[
        Path | None, typer.Option("--data-path", help="Offline pinned dataset; SHA-256 checked.")
    ] = None,
    sample_id: Annotated[list[str] | None, typer.Option("--sample-id")] = None,
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
    timeout: Annotated[float, typer.Option("--timeout", min=0.001)] = 40,
    trace_timeout: Annotated[float, typer.Option("--trace-timeout", min=0)] = 10,
    grading_timeout: Annotated[float, typer.Option("--grading-timeout", min=0.001)] = 180,
    content: Annotated[
        str, typer.Option("--content", help="metadata, redacted, or full; applied before persistence.")
    ] = "redacted",
    yes: Annotated[bool, typer.Option("--yes", help="Consent to agent and evaluator calls; NOT a cost cap.")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Evaluate your agent, without a built-in solver, Docker or retries."""
    if not yes:
        _fail("External evaluation requires explicit --yes before any agent or evaluator invocation.")
    if trace_store and trace_url or trace_token_env and not trace_url:
        _fail("Choose either --trace-store or --trace-url; trace credentials require --trace-url.")
    from everybench.evaluation import HttpAgent, HTTPTraceSource, StoreTraceSource, evaluate_sync, report_path

    try:
        import os

        trace_key = os.environ.get(trace_token_env) if trace_token_env else None
        if trace_token_env and not trace_key:
            _fail("The trace token environment variable is not set.")
        if token_env and not os.environ.get(token_env):
            _fail("The agent token environment variable is not set.")
        source = (
            HTTPTraceSource(trace_url, api_key=trace_key, workspace_id=workspace_id)
            if trace_url
            else StoreTraceSource(workspace_id=workspace_id)
            if trace_store
            else None
        )
        target = HttpAgent(url, name=name, version=version, token_env=token_env, timeout=timeout)
        report = evaluate_sync(
            benchmark,
            target,
            trace_source=source,
            allow_paid=yes,
            sample_ids=sample_id,
            limit=limit,
            timeout=timeout,
            trace_timeout=trace_timeout,
            grading_timeout=grading_timeout,
            content_policy=content,
            workspace_id=workspace_id,
            judge_models=judge,
            extractor_model=extractor,
            data_path=data_path,
        )
    except ValueError:
        _fail(
            "Invalid evaluation configuration, dataset or evaluator client; no valid report produced. "
            "External finance supports openai/openrouter/anthropic and registered mock models. "
            "Install everybench[finance] and configure explicit evaluator credentials."
        )
    except KeyboardInterrupt:
        _fail("Evaluation cancelled; inspect the persisted cancelled run/report.", code=130)
    if as_json:
        print(report.model_dump_json())
    else:
        console.print(f"{report.run_id}: {report.status}; report: {report_path(report.run_id)}")
    if report.status != "succeeded":
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
