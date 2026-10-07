"""Import traces from Langfuse, LangSmith, Braintrust, OTLP files and Inspect logs.

Imported traces keep their original ids as `source_ref` and are stored as
normalized spans, so they open in the same trace view and can be drafted into
datasets. Re-importing the same trace replaces it rather than duplicating it.
Historical outputs and scores are kept as evidence; they never become reference
answers on their own.

Credentials come from the server's environment:

    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST (default https://cloud.langfuse.com)
    LANGSMITH_API_KEY, LANGSMITH_ENDPOINT (default https://api.smith.langchain.com)
    BRAINTRUST_API_KEY, BRAINTRUST_API_URL (default https://api.braintrust.dev)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from benchtrace.db import ImportRun, Trace, now, session_scope
from benchtrace.otlp import store

USAGE_RIGHTS = ("own_data", "licensed", "unknown")
SOURCES = ("langfuse", "langsmith", "braintrust", "otlp_file", "inspect_log")

# (method, url, headers, json body) -> parsed JSON response
Fetch = Callable[[str, str, dict[str, str], Any], Any]


class ImportError_(RuntimeError):
    pass


def http_fetch(method: str, url: str, headers: dict[str, str], body: Any = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as ex:
        raise ImportError_(f"{method} {url} returned {ex.code}: {ex.read()[:300].decode(errors='replace')}") from ex


def _id(source: str, raw: str, length: int) -> str:
    return hashlib.sha256(f"{source}|{raw}".encode()).hexdigest()[:length]


def _ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(value: Any) -> Any:
    return value if value is None or isinstance(value, str) else json.loads(json.dumps(value, default=str))


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ImportError_(f"Set {name} on the server to import from this source.")
    return value


# ---------------------------------------------------------------------------- Langfuse

LANGFUSE_KINDS = {
    "GENERATION": "model",
    "SPAN": "span",
    "EVENT": "event",
    "AGENT": "agent",
    "TOOL": "tool",
    "CHAIN": "span",
    "RETRIEVER": "retriever",
    "EVALUATOR": "scorer",
    "EMBEDDING": "embedding",
    "GUARDRAIL": "guardrail",
}


def langfuse_traces(project: str | None, max_traces: int, fetch: Fetch) -> Iterator[tuple[str, list[dict]]]:
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
    token = base64.b64encode(f"{_env('LANGFUSE_PUBLIC_KEY')}:{_env('LANGFUSE_SECRET_KEY')}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}
    page, seen = 1, 0
    while seen < max_traces:
        params = {"page": page, "limit": min(50, max_traces - seen)}
        listing = fetch("GET", f"{host}/api/public/traces?{urllib.parse.urlencode(params)}", headers, None)
        rows = listing.get("data", [])
        if not rows:
            return
        for row in rows:
            detail = fetch("GET", f"{host}/api/public/traces/{urllib.parse.quote(row['id'])}", headers, None)
            yield row["id"], langfuse_spans(detail)
            seen += 1
            if seen >= max_traces:
                return
        meta = listing.get("meta", {})
        if page >= meta.get("totalPages", page):
            return
        page += 1


def langfuse_spans(trace: dict[str, Any]) -> list[dict[str, Any]]:
    root = _id("langfuse", trace["id"], 16)
    observations = trace.get("observations", [])
    starts = [_ts(o.get("startTime")) for o in observations if o.get("startTime")]
    ends = [_ts(o.get("endTime")) for o in observations if o.get("endTime")]
    start = _ts(trace.get("timestamp")) or (min(starts) if starts else None)
    spans = [
        {
            "span_id": root,
            "parent_id": None,
            "name": trace.get("name") or "trace",
            "kind": "agent",
            "status": "ok",
            "start_time": start,
            "end_time": max(ends) if ends else start,
            "attributes": {
                "langfuse.trace_id": trace["id"],
                "tags": trace.get("tags") or [],
                "session.id": trace.get("sessionId"),
                "release": trace.get("release"),
            },
            "content": {
                "input": _text(trace.get("input")),
                "output": _text(trace.get("output")),
                "metadata": _text(trace.get("metadata")),
            },
        }
    ]
    for o in observations:
        usage = o.get("usageDetails") or o.get("usage") or {}
        spans.append(
            {
                "span_id": _id("langfuse", o["id"], 16),
                "parent_id": _id("langfuse", o["parentObservationId"], 16) if o.get("parentObservationId") else root,
                "name": o.get("name") or o.get("type", "observation").lower(),
                "kind": LANGFUSE_KINDS.get(o.get("type", ""), "unknown"),
                "status": "error" if o.get("level") == "ERROR" else "ok",
                "start_time": _ts(o.get("startTime")),
                "end_time": _ts(o.get("endTime")) or _ts(o.get("startTime")),
                "attributes": {
                    "langfuse.observation_id": o["id"],
                    "gen_ai.request.model": o.get("model"),
                    "gen_ai.usage.input_tokens": usage.get("input"),
                    "gen_ai.usage.output_tokens": usage.get("output"),
                    "level": o.get("level"),
                    "status_message": o.get("statusMessage"),
                },
                "content": {"input": _text(o.get("input")), "output": _text(o.get("output"))},
            }
        )
    for sc in trace.get("scores", []):
        spans.append(
            {
                "span_id": _id("langfuse-score", sc.get("id", sc.get("name", "")), 16),
                "parent_id": _id("langfuse", sc["observationId"], 16) if sc.get("observationId") else root,
                "name": sc.get("name", "score"),
                "kind": "scorer",
                "status": "ok",
                "start_time": _ts(sc.get("timestamp")) or start,
                "end_time": _ts(sc.get("timestamp")) or start,
                "attributes": {"score.value": sc.get("value"), "score.source": sc.get("source")},
                "content": {"comment": sc.get("comment")},
            }
        )
    return spans


# ---------------------------------------------------------------------------- LangSmith

LANGSMITH_KINDS = {
    "llm": "model",
    "tool": "tool",
    "chain": "span",
    "retriever": "retriever",
    "embedding": "embedding",
    "prompt": "span",
    "parser": "span",
}


def langsmith_traces(project: str | None, max_traces: int, fetch: Fetch) -> Iterator[tuple[str, list[dict]]]:
    if not project:
        raise ImportError_("Give the LangSmith project name.")
    host = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com").rstrip("/")
    headers = {"x-api-key": _env("LANGSMITH_API_KEY")}
    sessions = fetch("GET", f"{host}/api/v1/sessions?{urllib.parse.urlencode({'name': project})}", headers, None)
    if not sessions:
        raise ImportError_(f"LangSmith project {project!r} not found.")
    session_id = sessions[0]["id"]

    roots: list[dict] = []
    cursor = None
    while len(roots) < max_traces:
        body = {"session": [session_id], "is_root": True, "limit": min(100, max_traces - len(roots))}
        if cursor:
            body["cursor"] = cursor
        page = fetch("POST", f"{host}/api/v1/runs/query", headers, body)
        roots.extend(page.get("runs", []))
        cursor = (page.get("cursors") or {}).get("next")
        if not cursor or not page.get("runs"):
            break
    for root in roots[:max_traces]:
        runs, cursor = [], None
        while True:
            body = {"session": [session_id], "trace": root["trace_id"], "limit": 100}
            if cursor:
                body["cursor"] = cursor
            page = fetch("POST", f"{host}/api/v1/runs/query", headers, body)
            runs.extend(page.get("runs", []))
            cursor = (page.get("cursors") or {}).get("next")
            if not cursor or not page.get("runs"):
                break
        yield root["trace_id"], langsmith_spans(runs or [root])


def langsmith_spans(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans = []
    for r in runs:
        metadata = (r.get("extra") or {}).get("metadata") or {}
        spans.append(
            {
                "span_id": _id("langsmith", r["id"], 16),
                "parent_id": _id("langsmith", r["parent_run_id"], 16) if r.get("parent_run_id") else None,
                "name": r.get("name") or r.get("run_type", "run"),
                "kind": "agent"
                if not r.get("parent_run_id")
                else LANGSMITH_KINDS.get(r.get("run_type", ""), "unknown"),
                "status": "error" if r.get("error") else "ok",
                "start_time": _ts(r.get("start_time")),
                "end_time": _ts(r.get("end_time")) or _ts(r.get("start_time")),
                "attributes": {
                    "langsmith.run_id": r["id"],
                    "langsmith.run_type": r.get("run_type"),
                    "gen_ai.request.model": metadata.get("ls_model_name"),
                    "gen_ai.usage.input_tokens": r.get("prompt_tokens"),
                    "gen_ai.usage.output_tokens": r.get("completion_tokens"),
                    "feedback": r.get("feedback_stats"),
                },
                "content": {
                    "input": _text(r.get("inputs")),
                    "output": _text(r.get("outputs")),
                    "error": r.get("error"),
                },
            }
        )
    return spans


# ---------------------------------------------------------------------------- Braintrust

BRAINTRUST_KINDS = {
    "llm": "model",
    "tool": "tool",
    "score": "scorer",
    "eval": "agent",
    "task": "agent",
    "function": "span",
    "classifier": "scorer",
    "preprocessor": "span",
    "automation": "span",
}


def braintrust_traces(project: str | None, max_traces: int, fetch: Fetch) -> Iterator[tuple[str, list[dict]]]:
    if not project:
        raise ImportError_("Give the Braintrust project name or id.")
    host = os.environ.get("BRAINTRUST_API_URL", "https://api.braintrust.dev").rstrip("/")
    headers = {"Authorization": f"Bearer {_env('BRAINTRUST_API_KEY')}"}
    project_id = project
    if len(project) != 36:  # not a UUID: look up by name
        found = fetch("GET", f"{host}/v1/project?{urllib.parse.urlencode({'project_name': project})}", headers, None)
        objects = found.get("objects", [])
        if not objects:
            raise ImportError_(f"Braintrust project {project!r} not found.")
        project_id = objects[0]["id"]

    by_root: dict[str, list[dict]] = defaultdict(list)
    cursor = None
    while True:
        body: dict[str, Any] = {"limit": 500}
        if cursor:
            body["cursor"] = cursor
        page = fetch("POST", f"{host}/v1/project_logs/{project_id}/fetch", headers, body)
        events = page.get("events", [])
        for ev in events:
            by_root[ev.get("root_span_id") or ev["span_id"]].append(ev)
        cursor = page.get("cursor")
        if not cursor or not events or len(by_root) >= max_traces:
            break
    for root_id in list(by_root)[:max_traces]:
        yield root_id, braintrust_spans(by_root[root_id])


def braintrust_spans(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans = []
    for ev in events:
        attrs = ev.get("span_attributes") or {}
        metrics = ev.get("metrics") or {}
        parents = ev.get("span_parents") or []
        scores = {f"score.{k}": v for k, v in (ev.get("scores") or {}).items()}
        spans.append(
            {
                "span_id": _id("braintrust", ev["span_id"], 16),
                "parent_id": _id("braintrust", parents[0], 16) if parents else None,
                "name": attrs.get("name") or attrs.get("type") or "span",
                "kind": "agent" if not parents else BRAINTRUST_KINDS.get(attrs.get("type") or "", "unknown"),
                "status": "error" if ev.get("error") else "ok",
                "start_time": _ts(metrics.get("start")),
                "end_time": _ts(metrics.get("end")) or _ts(metrics.get("start")),
                "attributes": {
                    "braintrust.id": ev.get("id"),
                    "braintrust.span_type": attrs.get("type"),
                    "gen_ai.usage.input_tokens": metrics.get("prompt_tokens"),
                    "gen_ai.usage.output_tokens": metrics.get("completion_tokens"),
                    "gen_ai.request.model": (ev.get("metadata") or {}).get("model"),
                    **scores,
                },
                "content": {
                    "input": _text(ev.get("input")),
                    "output": _text(ev.get("output")),
                    "expected": _text(ev.get("expected")),
                    "error": _text(ev.get("error")),
                },
            }
        )
    return spans


IMPORTERS: dict[str, Callable[[str | None, int, Fetch], Iterator[tuple[str, list[dict]]]]] = {
    "langfuse": langfuse_traces,
    "langsmith": langsmith_traces,
    "braintrust": braintrust_traces,
}


# ---------------------------------------------------------------------------- storage and execution


def store_imported(
    session: Session,
    source: str,
    source_ref: str,
    spans: list[dict[str, Any]],
    *,
    policy: str,
    workspace_id: str | None,
    import_id: str | None = None,
    usage_rights: str = "unknown",
) -> str:
    trace_id = _id(source, source_ref, 32)
    for span in spans:
        span["trace_id"] = trace_id
        span["attributes"] = {k: v for k, v in span["attributes"].items() if v is not None}
        if span["content"]:
            span["content"] = {k: v for k, v in span["content"].items() if v is not None} or None
    store(session, spans, policy=policy, source=f"import:{source}", workspace_id=workspace_id)
    trace = session.get(Trace, trace_id)
    trace.source_ref = source_ref
    trace.state = "imported"
    trace.attributes = {**(trace.attributes or {}), "import_id": import_id, "usage_rights": usage_rights}
    return trace_id


def create_import(
    source: str,
    *,
    project: str | None,
    usage_rights: str,
    content_policy: str = "redacted",
    options: dict[str, Any] | None = None,
    workspace_id: str | None = None,
) -> ImportRun:
    if source not in SOURCES:
        raise ValueError(f"Source must be one of {', '.join(SOURCES)}.")
    if usage_rights not in USAGE_RIGHTS:
        raise ValueError(f"Declare usage rights: one of {', '.join(USAGE_RIGHTS)}.")
    with session_scope() as session:
        run = ImportRun(
            source=source,
            project=project,
            usage_rights=usage_rights,
            content_policy=content_policy,
            options=options or {},
            workspace_id=workspace_id,
        )
        session.add(run)
        session.flush()
    return run


def execute_import(import_id: str, fetch: Fetch = http_fetch) -> ImportRun:
    with session_scope() as session:
        run = session.get(ImportRun, import_id)
        run.status = "running"
        source, project, options = run.source, run.project, dict(run.options or {})
        policy, workspace_id, rights = run.content_policy, run.workspace_id, run.usage_rights
    traces = spans = 0
    try:
        if source in IMPORTERS:
            for ref, items in IMPORTERS[source](project, int(options.get("max_traces", 100)), fetch):
                with session_scope() as session:
                    store_imported(
                        session,
                        source,
                        ref,
                        items,
                        policy=policy,
                        workspace_id=workspace_id,
                        import_id=import_id,
                        usage_rights=rights,
                    )
                traces += 1
                spans += len(items)
        elif source == "otlp_file":
            traces, spans = _import_otlp_file(Path(options["path"]), policy, workspace_id)
        elif source == "inspect_log":
            traces, spans = _import_inspect_log(Path(options["path"]), policy, workspace_id)
        status, error = "succeeded", None
    except Exception as ex:  # noqa: BLE001 - record any failure on the import
        status, error = "failed", f"{type(ex).__name__}: {ex}"
    with session_scope() as session:
        run = session.get(ImportRun, import_id)
        run.status, run.error, run.finished_at = status, error, now()
        run.traces_imported, run.spans_imported = traces, spans
    return run


def _import_otlp_file(path: Path, policy: str, workspace_id: str | None) -> tuple[int, int]:
    """Import OTLP/JSON: one ExportTraceServiceRequest per file, or one per line (JSONL)."""
    from benchtrace.otlp import normalize, parse_request

    text = path.read_text()
    chunks = (
        [text]
        if text.lstrip().startswith("{") and "\n{" not in text.strip()
        else [line for line in text.splitlines() if line.strip()]
    )
    traces: set[str] = set()
    spans = 0
    for chunk in chunks:
        normalized = normalize(parse_request(chunk.encode(), "application/json"))
        with session_scope() as session:
            traces |= store(session, normalized, policy=policy, source="import:otlp_file", workspace_id=workspace_id)
        spans += len(normalized)
    return len(traces), spans


def _import_inspect_log(path: Path, policy: str, workspace_id: str | None) -> tuple[int, int]:
    """Import an existing Inspect log as a run, with results and traces."""
    import hashlib as _h

    from inspect_ai.log import read_eval_log

    from benchtrace.db import Run
    from benchtrace.ingest import store_sample
    from benchtrace.inspect_convert import convert_sample

    log = read_eval_log(str(path))
    identity = {
        "task": log.eval.task,
        "task_args": log.eval.task_args,
        "scorers": [s.name for s in (log.results.scores if log.results else [])],
    }
    variant = _h.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:16]
    with session_scope() as session:
        run = Run(
            workspace_id=workspace_id,
            benchmark=f"external:{log.eval.task}",
            variant_key=variant,
            model=log.eval.model,
            status="succeeded" if log.status == "success" else log.status,
            content_policy=policy,
            log_path=str(path),
            samples_total=len(log.samples or []),
            manifest={"purpose": "import", "inspect": identity, "packages": log.eval.packages},
        )
        session.add(run)
        session.flush()
        spans = 0
        for sample in log.samples or []:
            converted = convert_sample(sample, run.id)
            store_sample(session, run, converted)
            spans += len(converted["spans"])
        if log.results:
            run.metrics = {s.name: {k: m.value for k, m in s.metrics.items()} for s in log.results.scores}
        run.finished_at = now()
        return len(log.samples or []), spans
