"""everyeval command-line interface."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree
from sqlalchemy import select

from everyeval.catalog import CatalogError, get_benchmark, load_catalog
from everyeval.compare import compare_runs
from everyeval.config import env
from everyeval.db import Quote, Run, SampleResult, Span, Trace, session_scope
from everyeval.execution import execute_run, request_cancel
from everyeval.service import PlanError, approve_quote, create_quote, create_runs, is_paid, runs_from_quote
from everyeval.trace_diff import content_field

app = typer.Typer(
    help="Run benchmarks against any model, inspect traces, and compare runs.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()
err = Console(stderr=True)

ModelOpt = Annotated[list[str], typer.Option("--model", "-m", help="Model to evaluate; repeat for several.")]
AgentOpt = Annotated[
    str | None,
    typer.Option(
        "--agent",
        help="Your own agent for an agent benchmark, as an import path (package.module:AgentClass) "
        "importable from the current directory. Runs local code.",
    ),
]


def _who(model: str, agent: str | None) -> str:
    """A run's model, with its custom agent's class name when there is one."""
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


def _confirm_paid(models: list[str], yes: bool, what: str) -> None:
    paid = [m for m in models if is_paid(m)]
    if paid and not yes:
        if not sys.stdin.isatty():
            _fail(f"{what} calls paid models ({', '.join(paid)}). Pass --yes to approve.")
        typer.confirm(f"{what} calls paid models ({', '.join(paid)}). Continue?", abort=True)


# ---------------------------------------------------------------------------- catalog


@app.command()
def catalog(
    benchmark: Annotated[str | None, typer.Argument(help="Show details for one benchmark.")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List benchmarks in the catalog, or show one."""
    if benchmark:
        try:
            entry = get_benchmark(benchmark)
        except CatalogError as ex:
            _fail(str(ex))
        data = entry.model_dump() | {"ref": entry.ref, "variant_key": entry.variant_key}
        if as_json:
            print(json.dumps(data))
            return
        table = Table(show_header=False, box=None)
        for key, value in data.items():
            if value not in (None, [], {}):
                table.add_row(f"[dim]{key}[/dim]", escape(str(value)))
        console.print(table)
        return
    entries = sorted(load_catalog().values(), key=lambda e: (not e.offline, e.id))
    if as_json:
        print(json.dumps([e.model_dump() | {"ref": e.ref} for e in entries]))
        return
    table = Table(title="Benchmark catalog")
    for col in ("Benchmark", "Family", "Variant", "Tasks", "Grader", "Needs"):
        table.add_column(col, overflow="fold")
    for e in entries:
        needs = ", ".join(e.requires + (["docker"] if e.sandbox else []) + (["offline"] if e.offline else []))
        table.add_row(e.ref, e.family, e.variant, str(e.task_count or "?"), e.grader, needs)
    console.print(table)
    families = len({e.family for e in entries})
    console.print(
        f"{len(entries)} variants across {families} benchmark families. "
        "Real benchmarks need `uv sync --extra benchmarks` (inspect_evals)."
    )


# ---------------------------------------------------------------------------- quote

quote_app = typer.Typer(help="Estimate cost before running, and approve a budget.", no_args_is_help=True)
app.add_typer(quote_app, name="quote")


def _print_quote(quote: Quote) -> None:
    agent = f" · agent {quote.agent}" if quote.agent else ""
    table = Table(title=f"Quote {quote.id} · {quote.benchmark}{agent} · {quote.samples_planned or '?'} samples planned")
    for col in ("Model", "Sample", "Tokens in (est.)", "Tokens out (est.)", "Cost (est.)", "95% range", "Notes"):
        table.add_column(col)
    for model, est in quote.estimate.items():
        rng = est.get("cost_usd_range")
        table.add_row(
            model,
            f"{est.get('samples_measured', 0)} ok / {est.get('sample_errors', 0)} err",
            f"{est.get('input_tokens', 0):,.0f}" if "input_tokens" in est else "–",
            f"{est.get('output_tokens', 0):,.0f}" if "output_tokens" in est else "–",
            _money(est.get("cost_usd")),
            f"{_money(rng[0])} – {_money(rng[1])}" if rng else "–",
            escape(est.get("note", "")),
        )
    console.print(table)
    total = sum((e.get("cost_usd_range") or [0, 0])[1] for e in quote.estimate.values())
    console.print(
        f"Upper estimate across models: {_money(total)}. Status: [bold]{quote.status}[/bold]"
        + (f", cap {_money(quote.cap_usd)}" if quote.cap_usd is not None else "")
    )


@quote_app.command("create")
def quote_create(
    benchmark: str,
    model: ModelOpt,
    limit: Annotated[int | None, typer.Option(help="Plan to run only the first N samples.")] = None,
    sample_size: Annotated[int, typer.Option(help="Samples to run per model for the estimate.")] = 5,
    agent: AgentOpt = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Approve paid calls for the sample run.")] = False,
) -> None:
    """Run a small sample and estimate tokens and cost for the full run."""
    try:
        entry = get_benchmark(benchmark)
    except CatalogError as ex:
        _fail(str(ex))
    _confirm_paid(model, yes, f"The quote runs {sample_size} samples per model and")
    with console.status(f"Running {sample_size} sample(s) per model to estimate cost..."):
        try:
            quote = create_quote(entry, model, limit=limit, sample_size=sample_size, agent=agent)
        except PlanError as ex:
            _fail(str(ex))
    _print_quote(quote)
    console.print(f"Approve with: [bold]everyeval quote approve {quote.id} --cap <usd>[/bold]")


@quote_app.command("approve")
def quote_approve(
    quote_id: str,
    cap: Annotated[float | None, typer.Option(help="Budget cap in USD across all models.")] = None,
    by: Annotated[str, typer.Option(help="Who approved.")] = "cli",
) -> None:
    """Approve a quote, optionally with a budget cap."""
    try:
        quote = approve_quote(quote_id, cap, by)
    except LookupError as ex:
        _fail(str(ex))
    _print_quote(quote)
    console.print(f"Run it with: [bold]everyeval run --quote {quote.id}[/bold]")


@quote_app.command("show")
def quote_show(quote_id: str) -> None:
    """Show a quote."""
    with session_scope() as session:
        quote = session.get(Quote, quote_id)
        if quote is None:
            _fail(f"Quote {quote_id} not found")
    _print_quote(quote)


# ---------------------------------------------------------------------------- run


def _progress_table(runs: dict[str, Run]) -> Table:
    table = Table(title="Runs")
    for col in ("Run", "Model", "Status", "Progress", "Accuracy", "Errors", "Tokens", "Cost"):
        table.add_column(col)
    for run in runs.values():
        total = run.samples_total or 0
        bar_width = 20
        filled = int(bar_width * run.samples_done / total) if total else 0
        progress = f"{'█' * filled}{'░' * (bar_width - filled)} {run.samples_done}/{total or '?'}"
        color = {"succeeded": "green", "failed": "red", "budget_exceeded": "yellow", "cancelled": "yellow"}.get(
            run.status, "cyan"
        )
        table.add_row(
            run.id,
            _who(run.model, run.agent),
            f"[{color}]{run.status}[/{color}]",
            progress,
            _pct(_accuracy(run)),
            str(run.n_error),
            f"{run.input_tokens + run.output_tokens:,}",
            _money(run.cost_usd),
        )
    return table


def _execute_with_progress(runs: list[Run], parallel: bool) -> list[Run]:
    state: dict[str, Run] = {r.id: r for r in runs}
    lock = threading.Lock()

    def update(run: Run) -> None:
        with lock:
            state[run.id] = run

    with Live(_progress_table(state), console=console, refresh_per_second=4) as live:

        def work(run: Run) -> None:
            update(execute_run(run.id, on_progress=update))

        if parallel:
            threads = [threading.Thread(target=work, args=(r,), daemon=True) for r in runs]
            for t in threads:
                t.start()
            while any(t.is_alive() for t in threads):
                with lock:
                    live.update(_progress_table(state))
                for t in threads:
                    t.join(timeout=0.25)
        else:
            for run in runs:
                thread = threading.Thread(target=work, args=(run,), daemon=True)
                thread.start()
                while thread.is_alive():
                    with lock:
                        live.update(_progress_table(state))
                    thread.join(timeout=0.25)
        live.update(_progress_table(state))
    return list(state.values())


@app.command()
def run(
    benchmark: Annotated[str | None, typer.Argument(help="Catalog benchmark, e.g. gsm8k or gsm8k@1.")] = None,
    model: Annotated[list[str] | None, typer.Option("--model", "-m", help="Model; repeat for several.")] = None,
    limit: Annotated[int | None, typer.Option(help="Run only the first N samples.")] = None,
    epochs: Annotated[int, typer.Option(help="Repeat each sample N times.")] = 1,
    budget: Annotated[float | None, typer.Option(help="Budget cap in USD per run.")] = None,
    quote: Annotated[str | None, typer.Option(help="Run an approved quote.")] = None,
    agent: AgentOpt = None,
    content: Annotated[str, typer.Option(help="Content policy: full, redacted, or metadata.")] = "full",
    sequential: Annotated[bool, typer.Option(help="Run models one after another.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Approve paid model calls.")] = False,
) -> None:
    """Run a benchmark against one or more models, with live progress."""
    try:
        if quote:
            if agent:
                _fail("A quote fixes its agent; pass --agent to `quote create` instead.")
            runs = runs_from_quote(quote, content_policy=content, epochs=epochs)
        else:
            if not benchmark or not model:
                _fail("Give a benchmark and at least one --model, or --quote.")
            entry = get_benchmark(benchmark)
            _confirm_paid(model, yes, "This run")
            runs = create_runs(
                entry, model, limit=limit, epochs=epochs, budget_usd=budget, content_policy=content, agent=agent
            )
    except (CatalogError, PlanError, LookupError) as ex:
        _fail(str(ex))

    finished = _execute_with_progress(runs, parallel=not sequential)
    for r in finished:
        if r.error and r.status != "succeeded":
            err.print(f"[yellow]{r.id}[/yellow]: {escape(r.error.strip().splitlines()[0])}")
    if len(finished) >= 2:
        a, b = finished[0].id, finished[1].id
        console.print(f"\nCompare: [bold]everyeval compare {a} {b}[/bold]")
    else:
        console.print(f"\nDetails: [bold]everyeval show {finished[0].id}[/bold]")
    if any(r.status != "succeeded" for r in finished):
        raise typer.Exit(1)


@app.command()
def cancel(run_id: str) -> None:
    """Cancel a queued or running run."""
    try:
        run_ = request_cancel(run_id)
    except LookupError as ex:
        _fail(str(ex))
    console.print(f"{run_.id}: {run_.status}")


# ---------------------------------------------------------------------------- inspect results


@app.command("runs")
def list_runs(
    limit: Annotated[int, typer.Option(help="How many to show.")] = 20,
    include_quotes: Annotated[bool, typer.Option(help="Include quote sample runs.")] = False,
) -> None:
    """List recent runs."""
    with session_scope() as session:
        rows = session.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit * 3)).all()
        rows = [r for r in rows if include_quotes or (r.manifest or {}).get("purpose") != "quote"][:limit]
        table = Table(title="Recent runs")
        for col in ("Run", "Created", "Benchmark", "Model", "Status", "Samples", "Accuracy", "Errors", "Cost"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                r.id,
                r.created_at.strftime("%Y-%m-%d %H:%M"),
                r.benchmark,
                _who(r.model, r.agent),
                r.status,
                f"{r.samples_done}/{r.samples_total or '?'}",
                _pct(_accuracy(r)),
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
            ("model", r.model),
            ("agent", r.agent or "catalog default"),
            ("resolved models", ", ".join(r.resolved_models or []) or "–"),
            ("status", r.status),
            ("samples", f"{r.samples_done}/{r.samples_total or '?'}"),
            ("accuracy", _pct(_accuracy(r))),
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
                _fail("Give a sample id, e.g. `everyeval trace RUN arith-003`.")
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
    from everyeval.trace_diff import diff_traces

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
        title=f"{a['benchmark']}: A={_who(a['model'], a.get('agent'))} vs B={_who(b['model'], b.get('agent'))}"
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
            f"Inspect a change: [bold]everyeval trace {a['id']} {first.sample_id}[/bold] and "
            f"[bold]everyeval trace {b['id']} {first.sample_id}[/bold]"
        )


@app.command()
def export(
    run_id: str,
    fmt: Annotated[str, typer.Option("--format", help="json (everyeval) or eee (Every Eval Ever).")] = "json",
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
                "schema": "everyeval.results/v1",
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
            "everyeval",
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
    from everyeval.imports import create_import, execute_import

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
    from everyeval.datasets import create_dataset

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
    from everyeval.datasets import ReviewError, add_run_samples, add_traces

    try:
        items = add_traces(dataset_id, trace or [], split) if trace else []
        if run_id:
            items += add_run_samples(dataset_id, run_id, outcome or [], split)
    except (LookupError, ReviewError) as ex:
        _fail(str(ex))
    console.print(f"Added {len(items)} draft item(s). Review them before use: everyeval dataset show {dataset_id}")


@dataset_app.command("show")
def dataset_show(dataset_id: str) -> None:
    """List a dataset's items and their review status."""
    from everyeval.db import Dataset, DatasetItem

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
    from everyeval.datasets import ReviewError, review_item

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
    from everyeval.datasets import export_items

    rows = export_items(dataset_id, split)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    console.print(f"Wrote {len(rows)} approved item(s) to {out}")


admin_app = typer.Typer(
    help="Manage users, workspaces, API keys and secrets (server with EVERYEVAL_AUTH=1).", no_args_is_help=True
)
app.add_typer(admin_app, name="admin")


@admin_app.command("create-user")
def admin_create_user(
    email: str,
    workspace: Annotated[str, typer.Option(help="Workspace name; created if missing.")],
    role: Annotated[str, typer.Option(help="owner, member or viewer.")] = "owner",
) -> None:
    """Create a user (or add an existing one) in a workspace. Prompts for a password."""
    from everyeval.auth import AuthError, create_user

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
    from everyeval.auth import AuthError, create_api_key, ensure_workspace

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
    from everyeval.auth import AuthError, ensure_workspace, set_secret

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
    workers: Annotated[int, typer.Option(help="Embedded worker slots; 0 to use separate `everyeval worker`.")] = 2,
) -> None:
    """Start the API server and web UI."""
    import logging

    import uvicorn

    from everyeval.server import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if host not in ("127.0.0.1", "localhost", "::1") and env("AUTH") != "1":
        err.print("[yellow]Warning:[/yellow] binding beyond localhost without authentication enabled.")
    console.print(f"everyeval on http://{host}:{port}  (workers: {workers})")
    uvicorn.run(create_app(workers=workers), host=host, port=port, log_level="warning")


@app.command()
def worker(concurrency: Annotated[int, typer.Option(help="Jobs to run at once.")] = 2) -> None:
    """Run queued jobs (runs and quotes) from the database."""
    import logging

    from everyeval.jobs import Worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    w = Worker(concurrency=concurrency)
    console.print(f"Worker {w.name} with {concurrency} slot(s); Ctrl-C to stop.")
    w.run_forever()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
