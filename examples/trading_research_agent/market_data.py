"""Free, offline market data: 120 trading days of made-up daily closes for a small watchlist.

The prices are a seeded random walk, so everyone gets the same numbers and no API key is needed.
To use real data, replace `closes()` with a CSV download or a market data API; nothing else changes.
"""

from __future__ import annotations

import random

TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOG", "META", "NFLX"]
DAYS = 120


def closes(ticker: str) -> list[float]:
    """Daily closing prices for `ticker`, oldest first."""
    rng = random.Random(ticker)
    drift = rng.uniform(-0.004, 0.004)
    price = rng.uniform(80, 400)
    prices = []
    for _ in range(DAYS):
        price *= 1 + drift + rng.gauss(0, 0.02)
        prices.append(round(price, 2))
    return prices
