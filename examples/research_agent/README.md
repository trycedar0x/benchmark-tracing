# Research agent (outside EveryBench)

A small agent with its own process and URL. EveryBench does not contain this agent. The dossier is frozen (not live news). No API key.

v1 reads the headline only and answers `get more bullish`. v2 checks the 20-day trend and the earnings call and answers `bounce`. The version lives in this process: change `VERSION` in `agent.py`, or pass `--agent-version`, then restart. Run the same EveryBench URL again.

```bash
python examples/research_agent/agent.py
# http://127.0.0.1:8765/answer

python examples/research_agent/agent.py --agent-version v2
```

EveryBench posts each case as JSON `{"case_id", "question"}` and expects `{"answer", "checked", "note"}`.

```bash
uv run everybench run research-cases --agent-url http://127.0.0.1:8765/answer
```

In the web UI (`uv run everybench serve`), open New run, choose `research-cases@1`, paste the same URL, and click **Run agent URL**.
