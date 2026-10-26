"""Offline fixture for the shared SDK, not a user-agent implementation.

Run: python examples/evaluate_callable.py --yes
Replace the fixture callable/benchmark with your own implementation explicitly.
"""

import argparse

from everybench.sdk import BenchmarkCase, CallableAgent, GradeResult, evaluate_sync, report_path


class ExactFixture:
    name = "offline-exact-fixture"
    requires_trace = False

    def describe(self):
        return {"protocol": "offline-exact-v1", "grading": "exact"}

    def cases(self):
        return [BenchmarkCase(id="original-17", input="What is 2+2?", reference={"answer": "4"})]

    async def grade(self, case, result, evidence):
        return GradeResult(score=float(result.answer == case.reference["answer"]), protocol="offline-exact-v1")


def fixture_agent(case):
    # Deterministic test fixture; no model/provider/SEC calls.
    return "4"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Explicit invocation consent (offline fixture only).")
    args = parser.parse_args()
    report = evaluate_sync(
        ExactFixture(),
        CallableAgent(fixture_agent, name="offline-fixture", version="v1"),
        allow_paid=args.yes,
        content_policy="redacted",
    )
    print(report.model_dump_json(indent=2))
    print(f"Report: {report_path(report.run_id)}")
