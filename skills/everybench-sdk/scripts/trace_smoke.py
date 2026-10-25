"""Emit one offline SDK trace and report delivery health as JSON."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

from everybench.sdk import EveryBench


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=10, help="SDK flush timeout in seconds.")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be positive and finite")
    ee = EveryBench(
        endpoint=os.environ.get("EVERYBENCH_URL", "http://127.0.0.1:8321"),
        content="metadata",
        service_name="everybench-sdk-smoke",
        set_global=False,
    )
    try:
        with ee.trace("sdk-smoke", task_id="smoke-001") as root:
            trace_id = format(root.get_span_context().trace_id, "032x")
            with ee.span("stub-model", kind="model", **{"gen_ai.request.model": "stub"}) as span:
                ee.record(span, "prompt", "What is 17 * 23?")
                arguments = {"a": 17, "b": 23}
                ee.record(span, "output", arguments)
            with ee.span("multiply", kind="tool") as span:
                ee.record(span, "arguments", arguments)
                answer = arguments["a"] * arguments["b"]
                ee.record(span, "result", answer)
    finally:
        health = ee.close(timeout=args.timeout)
    receipt = health.receipts.get(trace_id)
    delivered = (
        health.pending_batches == 0
        and health.refused_spans == 0
        and health.rejected_batches == 0
        and receipt is not None
        and receipt["state"] == "closed"
    )
    print(
        json.dumps(
            {
                "schema": "everybench.sdk-smoke/v1",
                "decision": "delivered" if delivered else "delivery_incomplete",
                "trace_id": trace_id,
                "answer": answer,
                "health": health.to_dict(),
            }
        )
    )
    return 0 if delivered else 2


if __name__ == "__main__":
    sys.exit(main())
