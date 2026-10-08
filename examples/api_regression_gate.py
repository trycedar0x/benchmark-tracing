"""Start two runs over the HTTP API, wait for them, and fail when the candidate regresses.

    uv run everyeval serve                      # in another terminal
    uv run python examples/api_regression_gate.py
    uv run python examples/api_regression_gate.py --benchmark gsm8k@1 \
        --baseline openai/gpt-4o-mini --candidate anthropic/claude-haiku-4-5 --limit 50

Uses only the standard library, so it also works from CI images without everyeval installed.
With authentication on, set EVERYEVAL_API_KEY to a bt_... key. Paid models need an approved
quote over the API unless the server sets EVERYEVAL_REQUIRE_QUOTE=0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get("EVERYEVAL_URL", "http://127.0.0.1:8321").rstrip("/")
FINISHED = {"succeeded", "failed", "cancelled", "budget_exceeded"}


def api(method: str, path: str, body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if key := os.environ.get("EVERYEVAL_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as ex:
        sys.exit(f"{method} {path} failed ({ex.code}): {ex.read().decode(errors='replace')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", default="toy-arith")
    parser.add_argument("--baseline", default="mock/strong")
    parser.add_argument("--candidate", default="mock/weak")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-drop", type=float, default=0.0, help="Allowed drop in score, e.g. 0.02 for 2 points.")
    args = parser.parse_args()

    runs = api(
        "POST",
        "/api/runs",
        {"benchmark": args.benchmark, "models": [args.baseline, args.candidate], "limit": args.limit},
    )
    a, b = (r["id"] for r in runs)
    print(f"Started {a} ({args.baseline}) and {b} ({args.candidate})")

    while True:
        states = {run_id: api("GET", f"/api/runs/{run_id}") for run_id in (a, b)}
        print("  " + "   ".join(f"{r['model']}: {r['status']} {r['samples_done']}" for r in states.values()))
        if all(r["status"] in FINISHED for r in states.values()):
            break
        time.sleep(2)
    if any(r["status"] != "succeeded" for r in states.values()):
        sys.exit("A run did not succeed; see `everyeval show <run>`.")

    result = api("GET", f"/api/compare?a={a}&b={b}")
    if not result["compatibility"]["comparable"]:
        sys.exit(f"Runs are not comparable: {result['compatibility']['blocking']}")
    if not result["delta_ci95"]:
        sys.exit(f"Too few scored pairs to compare ({result['n_scored_pairs']}).")
    low, high = result["delta_ci95"]
    p = result["mcnemar_p"]  # None when scores are not plain right/wrong
    print(
        f"Score {result['mean_a']:.1%} → {result['mean_b']:.1%} "
        f"(Δ {result['delta'] * 100:+.1f} pts, 95% CI {low * 100:+.1f} to {high * 100:+.1f}, "
        f"McNemar p={'n/a' if p is None else f'{p:.3g}'})"
    )
    regressions = [r["sample_id"] for r in result["rows"] if r["change"] == "regression"]
    print(f"Regressed samples: {', '.join(regressions) or 'none'}")
    print(f"Inspect one: everyeval diff {a} {b} <sample>")

    # Fail only when the whole confidence interval is below the allowed drop: a clear regression, not noise.
    if high < -args.max_drop:
        sys.exit(1)


if __name__ == "__main__":
    main()
