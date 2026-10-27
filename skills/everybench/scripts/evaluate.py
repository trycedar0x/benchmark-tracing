"""Read-only, bounded JSON regression gate over two existing runs (standard library only)."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FINISHED = {"succeeded", "failed", "cancelled", "budget_exceeded"}


class Client:
    def __init__(self, url: str, api_key: str | None, deadline: float) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.deadline = deadline

    def api(self, method: str, path: str, body: dict | None = None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Evaluation deadline reached.")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        # Do not retry POSTs: a lost response may still have created paid runs.
        try:
            with urllib.request.urlopen(request, timeout=min(30, remaining)) as response:
                return json.load(response)
        except urllib.error.HTTPError as ex:
            # Do not echo response bodies: they may contain prompts or credentials.
            raise RuntimeError(f"{method} {path}: HTTP {ex.code}; inspect the server for details.") from None
        except urllib.error.URLError as ex:
            if isinstance(ex.reason, TimeoutError):
                raise TimeoutError("HTTP request timed out.") from None
            raise RuntimeError("Cannot reach everybench; check the URL, TLS and server health.") from None


def assess(comparison: dict, max_drop: float, min_pairs: int) -> tuple[str, str]:
    if not comparison["compatibility"]["comparable"]:
        return "blocked", "Runs are incompatible; inspect comparison.compatibility.blocking."
    if comparison["compatibility"]["warnings"]:
        return "blocked", "Compatibility warnings require review before a strict gate."
    if any(comparison["errors"].values()) or any(comparison["missing"].values()):
        return "blocked", "Execution errors or missing samples require review."
    if any(row["change"] in {"error", "missing"} for row in comparison["rows"]):
        return "blocked", "Some paired samples have no usable score."
    if comparison["n_scored_pairs"] < min_pairs or comparison["delta_ci95"] is None:
        return "blocked", "Insufficient scored pairs for this gate."
    if comparison["delta_ci95"][1] < -max_drop:
        return "regression", "The whole 95% interval is below the allowed score drop."
    return "pass", "No clear regression under this policy; this does not prove equivalence or improvement."


def evaluate(client: Client, run_ids: list[str], max_drop: float, min_pairs: int, report: dict) -> int:
    report["run_ids"] = run_ids
    a, b = (urllib.parse.quote(id_, safe="") for id_ in run_ids)
    while True:
        states = [client.api("GET", f"/api/runs/{run_id}") for run_id in (a, b)]
        report["run_statuses"] = {state["id"]: state["status"] for state in states}
        if all(state["status"] in FINISHED for state in states):
            break
        remaining = client.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Evaluation deadline reached; runs may still be active.")
        time.sleep(min(1, remaining))
    if any(state["status"] != "succeeded" for state in states):
        report.update(decision="blocked", reason="Both runs must succeed before comparison.")
        return 2
    if any(state["outcomes"].get(key, 0) for state in states for key in ("error", "cancelled")):
        report.update(decision="blocked", reason="Execution errors or cancelled samples require review.")
        return 2
    comparison = client.api("GET", "/api/compare?" + urllib.parse.urlencode({"a": a, "b": b}))
    report["comparison"] = comparison
    decision, reason = assess(comparison, max_drop, min_pairs)
    report.update(decision=decision, reason=reason)
    return {"pass": 0, "regression": 1, "blocked": 2}[decision]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Existing baseline run ID.")
    parser.add_argument("--candidate", required=True, help="Existing candidate run ID.")
    parser.add_argument("--timeout", type=float, default=300, help="Overall HTTP/polling deadline in seconds.")
    parser.add_argument("--max-drop", type=float, default=0, help="Allowed score drop as a fraction, e.g. 0.02.")
    parser.add_argument("--min-pairs", type=int, default=2)
    args = parser.parse_args(argv)
    report = {"schema": "everybench.agent-eval/v1", "decision": "error", "run_ids": []}
    code = 2
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0 or args.min_pairs < 2:
            raise ValueError("Use a positive timeout and min-pairs >= 2.")
        if not math.isfinite(args.max_drop) or not 0 <= args.max_drop <= 1:
            raise ValueError("max-drop must be a finite fraction between 0 and 1.")
        client = Client(
            os.environ.get("EVERYBENCH_URL", "http://127.0.0.1:8321"),
            os.environ.get("EVERYBENCH_API_KEY"),
            time.monotonic() + args.timeout,
        )
        code = evaluate(client, [args.baseline, args.candidate], args.max_drop, args.min_pairs, report)
    except TimeoutError:
        report.update(decision="timeout", reason="Deadline or HTTP timeout reached; inspect run_ids before retrying.")
    except (RuntimeError, ValueError) as ex:
        report.update(decision="error", reason=str(ex))
    except (KeyError, TypeError, OSError):
        report.update(decision="error", reason="Unexpected API response or transport failure; inspect server health.")
    print(json.dumps(report, allow_nan=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
