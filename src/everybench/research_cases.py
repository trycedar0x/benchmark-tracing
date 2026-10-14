"""Frozen research cases EveryBench sends to an external agent URL.

These are cases, not live news. The agent and its dossier run in another process.
"""

from __future__ import annotations

from typing import Any

_QUESTION = "Is yesterday a reason to be more bullish, or a bounce in a weaker tape? What did you check?"

CASES: list[dict[str, Any]] = [
    {
        "id": "case-1",
        "ask": "NVDA rose about 3% yesterday on an AI-demand headline. Should I be more bullish?",
        "missed": (
            "the last 20 trading days, which are down, or the earnings call, which was more cautious than expected"
        ),
        "question": (
            "NVDA rose about 3% yesterday on an AI-demand headline. "
            "The last 20 trading days are down, and the last earnings call was more cautious "
            "than the Street expected. " + _QUESTION
        ),
    },
    {
        "id": "case-2",
        "ask": "AMD rose about 4% yesterday on a chip-supply headline. Should I be more bullish?",
        "missed": "the last 20 trading days, which are down, or the earnings call, which cut the next-quarter outlook",
        "question": (
            "AMD rose about 4% yesterday on a chip-supply headline. "
            "The last 20 trading days are down, and the last earnings call cut the next-quarter outlook. " + _QUESTION
        ),
    },
    {
        "id": "case-3",
        "ask": "TSLA rose about 2% yesterday on a delivery headline. Should I be more bullish?",
        "missed": "the last 20 trading days, which are down, or the earnings call, which walked back the margin guide",
        "question": (
            "TSLA rose about 2% yesterday on a delivery headline. "
            "The last 20 trading days are down, and the last earnings call walked back the margin guide. " + _QUESTION
        ),
    },
    {
        "id": "case-4",
        "ask": "META rose about 1% yesterday on an ad-click headline. Should I be more bullish?",
        "missed": "the last 20 trading days, which are down, or the earnings call, which said ad demand is uneven",
        "question": (
            "META rose about 1% yesterday on an ad-click headline. "
            "The last 20 trading days are down, and the last earnings call said ad demand is uneven. " + _QUESTION
        ),
    },
    {
        "id": "case-5",
        "ask": "AMZN rose about 3% yesterday on a retail-sales headline. Should I be more bullish?",
        "missed": "the last 20 trading days, which are down, or the earnings call, which flagged margin pressure",
        "question": (
            "AMZN rose about 3% yesterday on a retail-sales headline. "
            "The last 20 trading days are down, and the last earnings call flagged margin pressure. " + _QUESTION
        ),
    },
    {
        "id": "case-6",
        "ask": "MSFT rose about 2% yesterday on a cloud-contract headline. Should I be more bullish?",
        "missed": (
            "the last 20 trading days, which are down, or the earnings call, "
            "which was more cautious on growth than expected"
        ),
        "question": (
            "MSFT rose about 2% yesterday on a cloud-contract headline. "
            "The last 20 trading days are down, and the last earnings call was more cautious on growth "
            "than the Street expected. " + _QUESTION
        ),
    },
]

# The careful reply has to say this, and has to have checked both sources.
ACCEPT = "bounce"
REQUIRED = ("trend_20d", "earnings_call")


def case_text(case: dict[str, Any], total: int | None = None) -> str:
    """Label shown with the question so a reader can see this is a frozen case."""
    index = next(i for i, item in enumerate(CASES, start=1) if item["id"] == case["id"])
    count = total if total is not None else len(CASES)
    label = f"Case {index} of {count}. Frozen case, not live news."
    return f"{label}\n\n{case['question']}"


def grade(reply: dict[str, Any]) -> tuple[str, float, str]:
    """Score one reply. Correct only when the verdict and the required checks are both present."""
    answer = str(reply.get("answer") or "").strip()
    checked = {str(item) for item in (reply.get("checked") or [])}
    missing = [name for name in REQUIRED if name not in checked]
    verdict_ok = answer.lower() == ACCEPT
    if verdict_ok and not missing:
        return "correct", 1.0, f"Answer is {answer!r}. Checked {', '.join(REQUIRED)}."
    reasons = []
    if not verdict_ok:
        reasons.append(f"answer {answer!r} is not {ACCEPT!r}")
    if missing:
        reasons.append("did not check " + " or ".join(missing))
    return "incorrect", 0.0, "; ".join(reasons) + "."
