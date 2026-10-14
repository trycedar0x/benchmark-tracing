"""A trading-research agent that runs outside EveryBench.

It is its own process and URL. EveryBench is not imported here. Point a run at
http://127.0.0.1:8765/answer and EveryBench posts each frozen case to that URL.

    python examples/research_agent/agent.py
    python examples/research_agent/agent.py --agent-version v2

v1 reads the headline only and answers "get more bullish".
v2 checks the 20-day trend and the earnings call and, when those are weak, answers "bounce".
Change the version in this file (or pass --agent-version) and restart, then run EveryBench again.
The prices and call notes below are frozen. This is not live news and not investment advice.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Active version. Restart this process after changing it.
VERSION = "v1"

# Frozen dossier. The agent looks these up; it does not call a market-data API.
DOSSIER = {
    "NVDA": {
        "headline": "rose about 3% on an AI-demand headline",
        "trend_20d": "down",
        "earnings_call": "more cautious than the Street expected",
        "earnings_tone": "cautious",
    },
    "AMD": {
        "headline": "rose about 4% on a chip-supply headline",
        "trend_20d": "down",
        "earnings_call": "cut the next-quarter outlook and sounded more cautious than expected",
        "earnings_tone": "cautious",
    },
    "TSLA": {
        "headline": "rose about 2% on a delivery headline",
        "trend_20d": "down",
        "earnings_call": "walked back the margin guide and was more cautious than expected",
        "earnings_tone": "cautious",
    },
    "META": {
        "headline": "rose about 1% on an ad-click headline",
        "trend_20d": "down",
        "earnings_call": "said ad demand is uneven and was more cautious than expected",
        "earnings_tone": "cautious",
    },
    "AMZN": {
        "headline": "rose about 3% on a retail-sales headline",
        "trend_20d": "down",
        "earnings_call": "flagged margin pressure and was more cautious than expected",
        "earnings_tone": "cautious",
    },
    "MSFT": {
        "headline": "rose about 2% on a cloud-contract headline",
        "trend_20d": "down",
        "earnings_call": "was more cautious on growth than the Street expected",
        "earnings_tone": "cautious",
    },
}


def read_headline(ticker: str) -> str:
    return DOSSIER[ticker]["headline"]


def trend_20d(ticker: str) -> str:
    return DOSSIER[ticker]["trend_20d"]


def earnings_call(ticker: str) -> str:
    return DOSSIER[ticker]["earnings_call"]


def find_ticker(question: str) -> str:
    for ticker in DOSSIER:
        if re.search(rf"\b{ticker}\b", question):
            return ticker
    raise KeyError("no ticker in the question")


def answer_v1(ticker: str) -> dict:
    headline = read_headline(ticker)
    return {
        "answer": "get more bullish",
        "checked": ["headline"],
        "note": f"Checked the headline only: {ticker} {headline}.",
    }


def answer_v2(ticker: str) -> dict:
    trend = trend_20d(ticker)
    call = earnings_call(ticker)
    tone = DOSSIER[ticker]["earnings_tone"]
    note = f"Checked trend_20d: {trend}. Checked earnings_call: {call}."
    if trend == "down" and tone == "cautious":
        answer = "bounce"
        note += " Yesterday is a bounce in a weaker tape, not a new reason to be more bullish."
    else:
        answer = "get more bullish"
    return {"answer": answer, "checked": ["trend_20d", "earnings_call"], "note": note}


def research(question: str, version: str) -> dict:
    ticker = find_ticker(question)
    if version == "v1":
        return answer_v1(ticker)
    if version == "v2":
        return answer_v2(ticker)
    raise ValueError(f"Unknown version {version!r}. Use v1 or v2.")


class Handler(BaseHTTPRequestHandler):
    version = VERSION

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0].rstrip("/") == "/health":
            self._send(200, {"ok": True, "version": self.version})
            return
        self._send(404, {"error": "GET /health or POST /answer"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ("/answer", ""):
            self._send(404, {"error": "POST /answer"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
            reply = research(str(payload.get("question") or ""), self.version)
        except (json.JSONDecodeError, KeyError, ValueError) as ex:
            self._send(400, {"error": str(ex)})
            return
        self._send(200, reply)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(host: str, port: int, version: str) -> ThreadingHTTPServer:
    handler = type("ResearchHandler", (Handler,), {"version": version})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the research agent outside EveryBench.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--agent-version", choices=("v1", "v2"), default=VERSION)
    args = parser.parse_args()
    server = make_server(args.host, args.port, args.agent_version)
    host, port = server.server_address[:2]
    print(f"research agent {args.agent_version} on http://{host}:{port}/answer", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
