# Trading research agent

A small research agent that answers questions about a stock watchlist, and an exam that scores it with everyeval. Runs offline: the market data is made up and no API key is needed. Not investment advice.

| File | What it is | Do you edit it? |
| --- | --- | --- |
| [`agent.py`](agent.py) | The agent: three tools (prices, % change, moving average) and the logic that answers a question | Yes: the numbers in `AGENT_VERSIONS` |
| [`market_data.py`](market_data.py) | 120 days of made-up prices for 8 tickers | Only to plug in real data |
| [`exam.py`](exam.py) | 30 questions with answers worked out from the data | No |
| [`catalog.yaml`](catalog.yaml) | Adds the exam to everyeval as the `trading-research` benchmark | No |

Run every command from the repository root.

## 1. Ask the agent a question

```bash
EVERYEVAL_INGEST_CONTENT=full uv run everyeval serve      # terminal 1: web UI at http://127.0.0.1:8321
uv run python examples/trading_research_agent/agent.py "Is NVDA trending UP or DOWN over the last 20 trading days?"
```

```text
Answer: DOWN. NVDA moved -3.55% over the last 20 day(s).
Trace 91087710c25c9720e30b473a7580bf54: closed (2/2 spans)
```

The question, each tool call and the answer appear under Traces in the web UI.

## 2. Score two versions of the agent

`AGENT_VERSIONS` in `agent.py` holds two versions. `trader-v1` judges a trend from yesterday's move only; `trader-v2` looks at the last 20 trading days. The model name picks the version (no model is actually called):

```bash
export EVERYEVAL_CATALOG=examples/trading_research_agent/catalog.yaml
uv run everyeval run trading-research -m mockllm/trader-v1 -m mockllm/trader-v2 --yes
uv run everyeval compare <v1-run> <v2-run>
```

```text
│ Score (30 paired) │ 50.0% │ 93.3% │ +43.3 pts │ +26.7 to +60.0 │ 0.000244  │
Improvements: 13   Regressions: 0
```

`--yes` is needed because everyeval treats any non-`mock/` model as paid; this one costs nothing.

## 3. Find out why an answer changed

```bash
uv run everyeval diff <v1-run> <v2-run> trend-NVDA
```

```text
│ → 1 │ tool price_change │ {'ticker': 'NVDA', 'days': 1} → 3.1 │ {'ticker': 'NVDA', 'days': 20} → -3.55 │
│ 2   │ scorer pattern    │ score 0.0 · answer UP              │ score 1.0 · answer DOWN                │
```

v1 saw NVDA up 3.1% yesterday and answered UP; over 20 days it fell 3.55%. Start `everyeval serve` with `EVERYEVAL_CATALOG` set to see the same runs, comparison and traces in the web UI.

v2 still misses two questions: it compares the price with a 20-day average when asked about the 50-day one. Add a `trader-v3` with `"average_days": 50` and run it to see the fix.
