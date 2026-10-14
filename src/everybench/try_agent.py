"""Run the example stock-research assistant and describe the result in plain language."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from everybench.research_cases import CASES

_HISTORY = "read_recent_history"


def _agent():
    path = Path(__file__).resolve().parents[2] / "examples" / "research_agent" / "agent.py"
    spec = importlib.util.spec_from_file_location("research_agent_example", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load the assistant from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_try() -> dict[str, Any]:
    agent = _agent()
    cases = []
    right = 0
    for case in CASES:
        result = agent.answer(case["ask"])
        said = str(result["answer"]).strip()
        checked_history = _HISTORY in result["tools"]
        if checked_history and said.lower() == "bounce":
            right += 1
            line = f'It answered "{said}." It checked the last 20 trading days and the earnings call.'
        else:
            line = f'It answered "{said}." It did not check {case["missed"]}.'
        cases.append({"question": case["ask"], "line": line})
    total = len(cases)
    return {"summary": f"It got {right} of {total} right.", "cases": cases}
