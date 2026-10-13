"""A simple trading research agent. Runs offline: no API keys, made-up market data.

Ask it a question about the watchlist and it looks up prices with its tools, then answers:

    uv run python examples/trading_research_agent/agent.py "Is NVDA trending UP or DOWN?"

If `everyeval serve` is running, every question is also sent to everyeval as a trace.
To score the agent on a set of questions, see README.md in this folder.
"""

from __future__ import annotations

import os
import re
import sys
import time

from market_data import TICKERS, closes

# Agent versions: change these numbers, no coding needed. The last one is used by default.
AGENT_VERSIONS = {
    # First try: judges the trend from yesterday's move only.
    "trader-v1": {"lookback_days": 1, "average_days": 20},
    # Looks at the whole last month (20 trading days) instead.
    "trader-v2": {"lookback_days": 20, "average_days": 20},
}


# Tools: small functions the agent calls to look things up.
def get_prices(ticker: str) -> list[float]:
    return closes(ticker)


def price_change(ticker: str, days: int) -> float:
    prices = closes(ticker)
    return round((prices[-1] / prices[-1 - days] - 1) * 100, 2)


def moving_average(ticker: str, days: int) -> float:
    return round(sum(closes(ticker)[-days:]) / days, 2)


TOOLS = {"get_prices": get_prices, "price_change": price_change, "moving_average": moving_average}


def research(question: str, version: str | None = None, steps: list | None = None) -> str:
    """Answer one question. Each tool call is appended to `steps` so it can be traced."""
    settings = AGENT_VERSIONS[version or list(AGENT_VERSIONS)[-1]]
    steps = steps if steps is not None else []

    def use(tool: str, **arguments):
        started = time.time()
        result = TOOLS[tool](**arguments)
        steps.append({"tool": tool, "arguments": arguments, "result": result, "start": started, "end": time.time()})
        return result

    tickers = [t for t in TICKERS if re.search(rf"\b{t}\b", question.upper())]
    if not tickers:
        return f"Answer: UNKNOWN. I only cover {', '.join(TICKERS)}."
    lookback, average_days = settings["lookback_days"], settings["average_days"]

    if "moving average" in question.lower():
        ticker = tickers[0]
        last = use("get_prices", ticker=ticker)[-1]
        average = use("moving_average", ticker=ticker, days=average_days)
        answer = "YES" if last > average else "NO"
        note = f"{ticker} closed at {last:.2f}; its {average_days}-day average is {average:.2f}."
    elif len(tickers) > 1:
        changes = {t: use("price_change", ticker=t, days=lookback) for t in tickers}
        answer = max(changes, key=changes.get)
        note = ", ".join(f"{t} {c:+.2f}%" for t, c in changes.items()) + f" over {lookback} day(s)."
    else:
        ticker = tickers[0]
        change = use("price_change", ticker=ticker, days=lookback)
        answer = "UP" if change > 0 else "DOWN"
        note = f"{ticker} moved {change:+.2f}% over the last {lookback} day(s)."
    return f"Answer: {answer}. {note}"


def main() -> None:
    question = " ".join(sys.argv[1:]) or "Is NVDA trending UP or DOWN over the last 20 trading days?"
    version = os.environ.get("AGENT_VERSION") or list(AGENT_VERSIONS)[-1]
    from everyeval.sdk import EveryEval

    ee = EveryEval(os.environ.get("EVERYEVAL_URL", "http://127.0.0.1:8321"), content="full")
    with ee.trace("trading-research", task_id=question[:60], **{"agent.version": version}) as root:
        ee.record(root, "input", question)
        steps: list = []
        answer = research(question, version, steps)
        for step in steps:
            with ee.span(step["tool"], kind="tool") as span:
                ee.record(span, "arguments", step["arguments"])
                ee.record(span, "result", str(step["result"])[-200:])
        ee.record(root, "output", answer)
    print(answer)
    health = ee.flush(timeout=5)
    for trace_id, receipt in health.receipts.items():
        print(f"Trace {trace_id}: {receipt['state']} ({receipt['received_spans']}/{receipt['expected_spans']} spans)")
    if health.pending_batches:
        print("everyeval server not reachable; the trace is saved and sent next time.")


if __name__ == "__main__":
    main()
