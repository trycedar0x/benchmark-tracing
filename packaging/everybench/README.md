# EveryBench

Run established benchmarks against any model, see a trace for every task, and compare runs with paired statistics. Open source, local-first, no account needed. By [Eva AI](https://evabench.dev/everybench).

```bash
uvx everybench catalog
uvx everybench run toy-arith -m mock/strong -m mock/weak
uvx everybench serve        # web UI at http://127.0.0.1:8321
```

This release installs [`everyeval`](https://pypi.org/project/everyeval/) under the new `everybench` command; the code is moving to this name. Source: [github.com/trycedar0x/benchmark-tracing](https://github.com/trycedar0x/benchmark-tracing).
