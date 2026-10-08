"""Dataset drafts: turn interesting traces into reviewed evaluation tasks.

A trace becomes a draft item with its input and what the system produced at
the time. The historical output is evidence, not a reference answer. A person
sets the reference answer, the split (dev or held-out test) and the usage
rights before an item can be approved. Only approved items are exported.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from everyeval.catalog import CatalogError, get_benchmark
from everyeval.db import Dataset, DatasetItem, Run, SampleResult, Span, Trace, now, session_scope
from everyeval.redact import redact

SPLITS = ("unassigned", "dev", "test")
RIGHTS = ("own_data", "licensed", "unknown")
# benchtrace.* keys come from traces recorded before the project was renamed.
INPUT_KEYS = (
    "input",
    "input.value",
    "everyeval.content.input",
    "everyeval.content.prompt",
    "benchtrace.content.input",
    "benchtrace.content.prompt",
    "gen_ai.input.messages",
)
OUTPUT_KEYS = (
    "output",
    "output.value",
    "everyeval.content.output",
    "benchtrace.content.output",
    "gen_ai.output.messages",
)


class ReviewError(ValueError):
    pass


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)


def _first(content: dict[str, Any] | None, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if content and content.get(key) is not None:
            return content[key]
    return None


def draft_from_trace(session: Session, trace_id: str) -> dict[str, Any]:
    trace = session.get(Trace, trace_id)
    if trace is None:
        raise LookupError(f"Trace {trace_id} not found")
    spans = session.scalars(select(Span).where(Span.trace_id == trace_id).order_by(Span.start_time, Span.id)).all()
    ids = {s.span_id for s in spans}
    root = next((s for s in spans if s.parent_id is None or s.parent_id not in ids), None)
    sample = session.scalars(select(SampleResult).where(SampleResult.trace_id == trace_id)).first()
    provenance: dict[str, Any] = {
        "trace_id": trace_id,
        "source": trace.source,
        "source_ref": trace.source_ref,
        "run_id": trace.run_id,
        "sample_id": trace.sample_id,
    }

    if sample is not None:
        run = session.get(Run, sample.run_id)
        input_, output, score = sample.input, sample.output or sample.answer, sample.score
        rights = "unknown"
        try:
            entry = get_benchmark(run.benchmark)
            rights = "licensed" if entry.license else "unknown"
            provenance |= {"benchmark": entry.ref, "license": entry.license, "target": sample.target}
        except CatalogError:
            provenance |= {"benchmark": run.benchmark}
        provenance |= {"model": run.model, "outcome": sample.outcome}
    else:
        model_spans = [s for s in spans if s.kind == "model"]
        input_ = _first(root.content if root else None, INPUT_KEYS)
        output = _first(root.content if root else None, OUTPUT_KEYS)
        if input_ is None and model_spans:
            input_ = _first(model_spans[0].content, INPUT_KEYS)
        if output is None and model_spans:
            output = _first(model_spans[-1].content, OUTPUT_KEYS)
        scores = [
            v
            for s in spans
            for k, v in (s.attributes or {}).items()
            if k.startswith("score") and isinstance(v, int | float) and not isinstance(v, bool)
        ]
        score = scores[0] if scores else None
        rights = (trace.attributes or {}).get("usage_rights") or "unknown"

    input_text, output_text = _as_text(input_), _as_text(output)
    provenance["digest"] = hashlib.sha256(f"{input_text}\x00{output_text}".encode()).hexdigest()
    return {
        "source_trace_id": trace_id,
        "source": trace.source,
        "input": input_text,
        "historical_output": output_text,
        "historical_score": score,
        "status": "draft" if input_text is not None else "needs_content",
        "usage_rights": rights if rights in RIGHTS else "unknown",
        "provenance": provenance,
    }


def create_dataset(name: str, description: str | None = None, workspace_id: str | None = None) -> Dataset:
    with session_scope() as session:
        ds = Dataset(name=name, description=description, workspace_id=workspace_id)
        session.add(ds)
        session.flush()
    return ds


def add_traces(dataset_id: str, trace_ids: list[str], split: str = "unassigned") -> list[DatasetItem]:
    if split not in SPLITS:
        raise ReviewError(f"Split must be one of {', '.join(SPLITS)}.")
    items = []
    with session_scope() as session:
        ds = session.get(Dataset, dataset_id)
        if ds is None:
            raise LookupError(f"Dataset {dataset_id} not found")
        existing = set(session.scalars(select(DatasetItem.source_trace_id).where(DatasetItem.dataset_id == dataset_id)))
        for trace_id in trace_ids:
            if trace_id in existing:
                continue
            trace = session.get(Trace, trace_id)
            if trace is None or trace.workspace_id != ds.workspace_id:
                raise LookupError(f"Trace {trace_id} not found")
            item = DatasetItem(dataset_id=dataset_id, split=split, **draft_from_trace(session, trace_id))
            session.add(item)
            items.append(item)
            existing.add(trace_id)
        session.flush()
    return items


def add_run_samples(dataset_id: str, run_id: str, outcomes: list[str], split: str = "unassigned") -> list[DatasetItem]:
    with session_scope() as session:
        query = select(SampleResult.trace_id).where(SampleResult.run_id == run_id, SampleResult.trace_id.is_not(None))
        if outcomes:
            query = query.where(SampleResult.outcome.in_(outcomes))
        trace_ids = list(session.scalars(query))
    return add_traces(dataset_id, trace_ids, split)


def review_item(
    item_id: int,
    *,
    reviewer: str,
    reference_output: str | None = None,
    split: str | None = None,
    usage_rights: str | None = None,
    notes: str | None = None,
    status: str | None = None,
) -> DatasetItem:
    with session_scope() as session:
        item = session.get(DatasetItem, item_id)
        if item is None:
            raise LookupError(f"Dataset item {item_id} not found")
        if reference_output is not None:
            item.reference_output = reference_output
        if split is not None:
            if split not in SPLITS:
                raise ReviewError(f"Split must be one of {', '.join(SPLITS)}.")
            item.split = split
        if usage_rights is not None:
            if usage_rights not in RIGHTS:
                raise ReviewError(f"Usage rights must be one of {', '.join(RIGHTS)}.")
            item.usage_rights = usage_rights
        if notes is not None:
            item.notes = notes
        if status == "approved":
            problems = []
            if not item.input:
                problems.append("the input is missing (content was withheld)")
            if not (item.reference_output or "").strip():
                problems.append("no reference answer is set")
            if item.split == "unassigned":
                problems.append("no split is assigned")
            if item.usage_rights == "unknown":
                problems.append("usage rights are unknown")
            if problems:
                raise ReviewError("Cannot approve: " + "; ".join(problems) + ".")
        if status is not None:
            if status not in ("draft", "approved", "rejected"):
                raise ReviewError("Status must be draft, approved or rejected.")
            item.status = status
            item.reviewed_at = now()
            item.reviewed_by = reviewer
        return item


def export_items(dataset_id: str, split: str | None = None, redact_content: bool = False) -> list[dict[str, Any]]:
    """Approved items as Inspect-compatible JSONL rows (input, target, id, metadata)."""
    with session_scope() as session:
        query = select(DatasetItem).where(DatasetItem.dataset_id == dataset_id, DatasetItem.status == "approved")
        if split:
            query = query.where(DatasetItem.split == split)
        rows = []
        for item in session.scalars(query.order_by(DatasetItem.id)):
            row = {
                "id": f"item-{item.id}",
                "input": item.input,
                "target": item.reference_output,
                "metadata": {
                    "split": item.split,
                    "usage_rights": item.usage_rights,
                    "source": item.source,
                    "provenance": item.provenance,
                },
            }
            rows.append(redact(row) if redact_content else row)
        return rows
