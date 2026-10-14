"""Send frozen cases to an agent URL and store the scored replies.

The agent is another process. This module only posts the case and grades the JSON reply.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from everybench.db import Run, now, session_scope
from everybench.ingest import store_sample
from everybench.inspect_convert import trace_id_for
from everybench.research_cases import ACCEPT, CASES, case_text, grade


def run_agent_url(run_id: str) -> int:
    with session_scope() as session:
        run = session.get(Run, run_id)
        url = (run.manifest or {}).get("agent_url") or run.model
        limit, epochs = run.limit, run.epochs
    cases = CASES[:limit] if limit else list(CASES)
    total = len(cases) * epochs
    with session_scope() as session:
        run = session.get(Run, run_id)
        run.samples_total = total

    status, error = "succeeded", None
    try:
        for epoch in range(1, epochs + 1):
            for case in cases:
                if _cancelled(run_id):
                    status = "cancelled"
                    break
                _one(run_id, url, case, epoch, len(cases))
            if status == "cancelled":
                break
    except Exception as ex:  # noqa: BLE001 - record any failure on the run
        status, error = "failed", f"{type(ex).__name__}: {ex}"

    with session_scope() as session:
        run = session.get(Run, run_id)
        if run.status == "cancelling":
            status = "cancelled"
        run.status = status
        run.finished_at = now()
        scored = run.samples_done - run.n_error
        run.metrics = {"agent_url": {"accuracy": (run.n_correct / scored) if scored else None}}
        if error:
            run.error = error
    return 0 if status == "succeeded" else 1


def _cancelled(run_id: str) -> bool:
    with session_scope() as session:
        return session.get(Run, run_id).status == "cancelling"


def _one(run_id: str, url: str, case: dict[str, Any], epoch: int, shown: int) -> None:
    prompt = case_text(case, shown)
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    reply: dict[str, Any] | None = None
    failure: str | None = None
    try:
        reply = _post(url, {"case_id": case["id"], "question": prompt})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as ex:
        failure = str(ex)
    finished = datetime.now(UTC)
    elapsed = time.perf_counter() - t0

    if failure is not None or reply is None:
        outcome, score, explanation = "error", None, failure or "empty reply"
        answer, output = None, None
    else:
        outcome, score, explanation = grade(reply)
        answer = str(reply.get("answer") or "").strip() or None
        output = _output(reply)

    trace_id = trace_id_for(run_id, case["id"], epoch)
    root_id = f"sample-{trace_id[:16]}"
    call_id = f"post-{trace_id[:16]}"
    converted = {
        "result": {
            "sample_id": case["id"],
            "epoch": epoch,
            "outcome": outcome,
            "score": score,
            "score_raw": None if score is None else str(score),
            "scorer": "research_cases",
            "answer": answer,
            "explanation": explanation,
            "target": ACCEPT,
            "input": prompt,
            "output": output,
            "error": failure,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_time": elapsed,
            "trace_id": trace_id,
        },
        "trace": {
            "trace_id": trace_id,
            "sample_id": case["id"],
            "name": case["id"],
            "span_count": 2,
            "state": "closed",
            "start_time": started,
            "end_time": finished,
            "attributes": {},
        },
        "spans": [
            {
                "span_id": root_id,
                "parent_id": None,
                "name": case["id"],
                "kind": "agent",
                "start_time": started,
                "end_time": finished,
                "status": "error" if failure else "ok",
                "attributes": {"sample.id": case["id"], "outcome": outcome, "score": score},
                "content": {"input": prompt, "output": output, "target": ACCEPT},
            },
            {
                "span_id": call_id,
                "parent_id": root_id,
                "name": "POST agent",
                "kind": "tool",
                "start_time": started,
                "end_time": finished,
                "status": "error" if failure else "ok",
                "attributes": {"agent.url": url},
                "content": {
                    "request": prompt,
                    "response": output if output is not None else failure,
                },
            },
        ],
        "resolved_models": {url},
    }
    with session_scope() as session:
        store_sample(session, session.get(Run, run_id), converted, source="agent-url")


def _output(reply: dict[str, Any]) -> str:
    checked = ", ".join(str(item) for item in (reply.get("checked") or [])) or "nothing"
    note = str(reply.get("note") or "").strip()
    lines = [str(reply.get("answer") or "").strip(), "", f"Checked: {checked}."]
    if note:
        lines += ["", note]
    return "\n".join(lines)


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode(errors="replace")[:500]
        raise ValueError(f"Agent returned HTTP {ex.code}: {detail}") from ex
    if not isinstance(data, dict) or "answer" not in data:
        raise ValueError("Agent reply must be a JSON object with an answer.")
    return data
