"""Paired comparison of two runs on the same benchmark variant.

Samples are aligned by (sample id, epoch). Execution errors are reported
separately and excluded from score deltas: an error is not a wrong answer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from scipy.stats import binomtest
from sqlalchemy import select

from benchtrace.db import Run, SampleResult, session_scope

BOOTSTRAP_RESAMPLES = 10_000


@dataclass
class Compatibility:
    comparable: bool = True
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PairRow:
    sample_id: str
    epoch: int
    a_outcome: str
    b_outcome: str
    a_score: float | None
    b_score: float | None
    change: str  # regression | improvement | same_correct | same_incorrect | changed_score | error | missing
    a_trace_id: str | None = None
    b_trace_id: str | None = None


@dataclass
class Comparison:
    run_a: dict[str, Any]
    run_b: dict[str, Any]
    compatibility: Compatibility
    n_paired: int = 0
    n_scored_pairs: int = 0
    mean_a: float | None = None
    mean_b: float | None = None
    delta: float | None = None
    delta_ci95: list[float] | None = None
    mcnemar_p: float | None = None
    discordant: dict[str, int] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    missing: dict[str, int] = field(default_factory=dict)
    rows: list[PairRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_summary(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "benchmark": run.benchmark,
        "model": run.model,
        "status": run.status,
        "resolved_models": run.resolved_models,
        "samples_done": run.samples_done,
        "variant_key": run.variant_key,
        "metrics": run.metrics,
    }


def check_compatibility(a: Run, b: Run, a_keys: set, b_keys: set) -> Compatibility:
    c = Compatibility()
    if a.benchmark != b.benchmark or a.variant_key != b.variant_key:
        c.blocking.append(
            f"Different benchmark variants: {a.benchmark} ({a.variant_key}) vs {b.benchmark} ({b.variant_key}). "
            "Scores from different variants are not directly comparable."
        )
    pkg_a = ((a.manifest or {}).get("versions") or {}).get("inspect_evals")
    pkg_b = ((b.manifest or {}).get("versions") or {}).get("inspect_evals")
    if pkg_a != pkg_b:
        c.warnings.append(
            f"Benchmark package versions differ (inspect_evals {pkg_a} vs {pkg_b}); "
            "prompts or graders may have changed."
        )
    if a.epochs != b.epochs:
        c.warnings.append(f"Different epochs ({a.epochs} vs {b.epochs}); only shared epochs are paired.")
    only_a, only_b = len(a_keys - b_keys), len(b_keys - a_keys)
    if only_a or only_b:
        c.warnings.append(f"Sample sets differ: {only_a} only in A, {only_b} only in B. Comparing the overlap.")
    if not (a_keys & b_keys):
        c.blocking.append("No samples in common.")
    for label, run in (("A", a), ("B", b)):
        if len(run.resolved_models or []) > 1:
            c.blocking.append(
                f"Run {label} ({run.id}) was served by more than one model revision: "
                f"{', '.join(run.resolved_models)}. Strict comparison is invalid; rerun or pin the model."
            )
        if run.status not in ("succeeded",):
            c.warnings.append(f"Run {label} ({run.id}) is {run.status}; results are partial.")
    c.comparable = not c.blocking
    return c


def _bootstrap_ci(diffs: np.ndarray, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diffs), size=(BOOTSTRAP_RESAMPLES, len(diffs)))
    means = diffs[idx].mean(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def _completed_samples(session: Any, run_id: str) -> dict[tuple[str, int], SampleResult]:
    query = select(SampleResult).where(SampleResult.run_id == run_id, SampleResult.outcome != "cancelled")
    return {(s.sample_id, s.epoch): s for s in session.scalars(query)}


def compare_runs(run_a_id: str, run_b_id: str, force: bool = False) -> Comparison:
    with session_scope() as session:
        a, b = session.get(Run, run_a_id), session.get(Run, run_b_id)
        if a is None or b is None:
            raise LookupError(f"Run {run_a_id if a is None else run_b_id} not found")
        sa, sb = _completed_samples(session, a.id), _completed_samples(session, b.id)
        compat = check_compatibility(a, b, set(sa), set(sb))
        result = Comparison(run_a=_run_summary(a), run_b=_run_summary(b), compatibility=compat)
        result.missing = {"only_a": len(set(sa) - set(sb)), "only_b": len(set(sb) - set(sa))}
        if not compat.comparable and not force:
            return result

        rows: list[PairRow] = []
        scored_a, scored_b = [], []
        for key in sorted(set(sa) & set(sb)):
            x, y = sa[key], sb[key]
            if "error" in (x.outcome, y.outcome):
                change = "error"
            elif x.score is None or y.score is None:
                change = "missing"
            else:
                scored_a.append(x.score)
                scored_b.append(y.score)
                if x.score == y.score:
                    change = "same_correct" if x.score >= 1 else "same_incorrect" if x.score <= 0 else "same_partial"
                elif x.outcome == "correct" and y.outcome == "incorrect":
                    change = "regression"
                elif x.outcome == "incorrect" and y.outcome == "correct":
                    change = "improvement"
                else:
                    change = "regression" if y.score < x.score else "improvement"
            rows.append(
                PairRow(
                    sample_id=key[0],
                    epoch=key[1],
                    a_outcome=x.outcome,
                    b_outcome=y.outcome,
                    a_score=x.score,
                    b_score=y.score,
                    change=change,
                    a_trace_id=x.trace_id,
                    b_trace_id=y.trace_id,
                )
            )

    result.rows = rows
    result.n_paired = len(rows)
    result.n_scored_pairs = len(scored_a)
    result.errors = {
        "a_only": sum(1 for r in rows if r.a_outcome == "error" and r.b_outcome != "error"),
        "b_only": sum(1 for r in rows if r.b_outcome == "error" and r.a_outcome != "error"),
        "both": sum(1 for r in rows if r.a_outcome == "error" and r.b_outcome == "error"),
    }
    if scored_a:
        xa, xb = np.array(scored_a, dtype=float), np.array(scored_b, dtype=float)
        result.mean_a, result.mean_b = float(xa.mean()), float(xb.mean())
        result.delta = result.mean_b - result.mean_a
        result.delta_ci95 = _bootstrap_ci(xb - xa) if len(xa) > 1 else None
        binary = set(np.unique(np.concatenate([xa, xb]))) <= {0.0, 1.0}
        if binary:
            improvements = int(((xa == 0) & (xb == 1)).sum())
            regressions = int(((xa == 1) & (xb == 0)).sum())
            result.discordant = {"improvements": improvements, "regressions": regressions}
            n = improvements + regressions
            result.mcnemar_p = float(binomtest(improvements, n, 0.5).pvalue) if n else 1.0
    return result
