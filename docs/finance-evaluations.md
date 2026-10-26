# Experimental finance evaluations

This is a development integration, **not a newly published supported benchmark collection**. The default catalog and UI do not list these tasks. Real-model/search/Docker end-to-end validation and comparison with upstream scoring outputs are still required before publication.

## What is implemented

- Immutable data revisions, SHA-256 verification, schema/count/ID checks, lazy local caching, license and attribution manifests.
- FinDeepIndicator: the public 3,350-item release; response extraction, formula judging, numeric/table comparisons, and separately retained component scores.
- BigFinanceBench: the **50-item public subset**, not the full 928-item benchmark; actual visible execution trace plus final answer are supplied to rubric judges. Weights are applied by code, not shown to the judges. Final-answer correctness is separate from rubric coverage.
- Inspect task registration, a fixed research tool surface, explicit evaluator model choices, model-role tagging, full policy-filtered sample evidence, and primary-score normalization.
- Offline tests using synthetic examples, deterministic models, mocked judges and mocked public-source tools. They do not demonstrate live financial research quality or official leaderboard parity.

## Protocol identity

These are explicitly named EveryBench adaptations:

- `everybench.findeepindicator.response-v1`
- `everybench.bigfinance-public-50.trace-rubric-v1`

The FinDeepIndicator implementation independently interprets the published evaluation contract; no unlicensed upstream code is copied. Its joint JSON extraction prompt is an adaptation, not a claim that all upstream extraction/judge choices are identical. The primary scalar is final-answer accuracy. Formula, raw-data and computed-series results are retained separately; non-applicable series/table metrics are not automatically converted to zero or averaged into a composite.

The BigFinance evaluator uses weighted rubric coverage as its primary score and separately reports final-answer correctness. A one-judge run is labeled `single-judge-adaptation`. Multiple judges do not by themselves reproduce the original headline protocol: jury snapshots, repeated trials, prompts, tools and aggregation also matter.

`solver_mode=answer_only` is a diagnostic mode, not the full research-tool setup. Both mode and task arguments belong to the variant. A final-answer-only rescore must not be presented as the complete trace-aware BigFinance evaluation.

## Data and licenses

| Dataset | Revision | SHA-256 | Public count | License |
| --- | --- | --- | --- | --- |
| FinDeepIndicator | `f59dad280fba4e736ccf346eb234265d13fc6737` | `5dcf594762b283cd2059204a2f43d9dedc3ef4d390f43349d122c6ffc6777343` | 3,350 | Apache-2.0, as declared by the official data card |
| BigFinance public subset | `3b13eecc4cacb4cfa13ac2b7c111d14ce127dd71` | `0c1b06b2cf80ddc85eec9e06913abae33a5ff4e27f936cb510dd6286083171c5` | 50 | CC BY 4.0 |

Sources: [FinDeepIndicator data card](https://huggingface.co/datasets/OpenFinArena/FinDeepIndicator/blob/f59dad280fba4e736ccf346eb234265d13fc6737/README.md), [BigFinance data license](https://github.com/Rogo-Technologies/big-finance-benchmark/blob/3b13eecc4cacb4cfa13ac2b7c111d14ce127dd71/data/LICENSE-DATA), [BigFinance reference grader](https://github.com/Rogo-Technologies/big-finance-benchmark/blob/3b13eecc4cacb4cfa13ac2b7c111d14ce127dd71/big_finance_harness/grader.py).

Data is downloaded only when a task is constructed, into `~/.everybench/benchmark-data/<dataset>/<revision>/`. Cache hits are rehashed. Changed or corrupted data fails closed rather than silently fetching a different revision. `source.json` records source, revision, hash, license and attribution. Original records retain evaluation-only/do-not-train flags and canaries; do not use evaluation data or outputs as training data contrary to those restrictions. Third-party financial source terms remain separate.

Agent inputs contain the question only. Reference answers, rubrics, formulas and data remain in evaluator targets/metadata and are not injected into model messages or sandbox files. The original Inspect log contains evaluation metadata and is sensitive; do not expose it as an agent-accessible file.

## Local development setup

Install PDF support; Docker is required for the network-disabled Python sandbox:

```bash
uv sync --extra finance
```

Copy and edit the development catalog. Replace **every** evaluator placeholder with a model ID/snapshot you have chosen and authorized. There are no default paid judge models:

```bash
cp examples/finance_catalog.yaml /tmp/everybench-finance.yaml
# Edit /tmp/everybench-finance.yaml before continuing.
export EVERYBENCH_CATALOG=/tmp/everybench-finance.yaml
export TAVILY_API_KEY=...
export SEC_EDGAR_USER_AGENT="Your Name your@email.com"
# Also configure the providers used by the evaluated model and evaluators.
export OPENAI_API_KEY=...
```

Inspect the internal entries explicitly:

```bash
uv run everybench catalog --include-internal
uv run everybench catalog findeepindicator-dev --include-internal
```

The following commands call paid model/judge APIs and potentially paid search services. Start with a deliberately small sample; `--limit` limits the number of questions, **not dollar spend per question**:

```bash
uv run everybench run findeepindicator-dev -m openai/YOUR_MODEL_SNAPSHOT --limit 2
uv run everybench run bigfinance-public50-dev -m openai/YOUR_MODEL_SNAPSHOT --limit 2
```

The CLI payment-confirmation check includes configured extractor/judge models, even if the evaluated model is a mock. In non-interactive scripts, `--yes` is explicit consent for those calls; do not add it casually.

The fixed tool surface is Tavily search, SEC EDGAR full-text search, public HTTPS document fetching with PDF/text extraction, and Inspect's sandboxed Python. Public-document fetching rejects local/private/reserved addresses, revalidates redirects, and pins connections to the checked address. It forwards no authentication headers or cookies. Generated Python has no host mounts, provider credentials or network access. The experimental Docker image uses a tag; pin and validate its digest before publishing a stable variant.

This is a baseline research agent, not automatic support for a user's external agent framework. An external agent requires an explicit adapter and real execution evidence.

## Costs and API safeguards

**Quotes and all-inclusive budget caps are disabled for these experimental tasks.** Existing token-only quote extrapolation is not a safe estimate of a multi-model research workflow with judge and search fees.

During finance runs, the generic `run.cost_usd` remains unknown rather than pricing every token at the evaluated model's rate. At reconciliation, `run.metrics.finance` contains per-model and per-role usage, configured LLM price estimates where available, component averages/counts, and an explicit statement that search/data fees are not included or capped. Missing model prices stay unknown.

The normal server policy still requires an approved quote for any paid evaluated or evaluator model. Since finance quotes are disabled, do not expect paid finance runs through the default API policy. The development CLI is the intended pilot path. Do not disable quote enforcement on a shared server just to bypass this limitation.

## Full evidence and privacy

Every new Inspect sample has a separate evidence artifact containing its full visible messages/tool results, final answer, all scorer values/metadata, and model/role usage. It does not collect private model reasoning. The UI span preview is still truncated independently.

Retrieve evidence through the workspace-authorized API:

```text
GET /api/runs/<run-id>/sample-evidence?sample_id=<url-encoded-id>&epoch=1
```

A missing artifact returns 404, including for old runs without artifacts. Paths are hashed, files are owner-readable only, and content policy is applied before artifact bytes are written:

- `full`: complete evidence.
- `redacted`: complete structure with best-effort redaction.
- `metadata`: content withheld; no full evidence is retained in this artifact.

Judges necessarily see the evidence used for scoring during execution. This is separate from the storage policy: configure only evaluators/providers you are authorized to send that data to. Original Inspect logs remain full, as documented for all benchmark runs; the storage policy is not a promise that the original runner logs are sanitized. Persistent artifacts are operator evidence, not a new source of input for the evaluated agent.

## Score conversion

Catalog entries may set `scoring.scorer`, `scoring.key`, `scoring.minimum`, `scoring.maximum` and `scoring.direction`. Non-default scoring enters the variant identity. Finance tasks also declare their primary selection in sample metadata so imported Inspect logs retain it.

Unsupported structured values, unknown score strings, non-finite values, missing selected fields and out-of-range values become an explicit `unscored` sample with a diagnostic, never a silent valid zero. Valid scores are normalized to 0–1 before the existing UI/paired comparison sees them. All scorer evidence remains available in policy-filtered artifacts; raw structured values are withheld under metadata-only storage.

Evaluator infrastructure/format errors fail explicitly. A genuinely wrong answer is a valid zero, while non-applicable component metrics stay non-applicable.

## Validation and release gate

Run local tests without paid services:

```bash
uv run pytest tests/test_scoring.py tests/test_artifacts.py tests/test_finance_datasets.py tests/test_finance_scoring.py tests/test_finance_integration.py tests/test_finance_tools.py
uv run ruff check src tests
```

Before public release, still verify live search/PDF/EDGAR behavior, Docker isolation and image pinning, real-model and judge runs, upstream scoring consistency, missing-output/error handling, all cost surfaces and safe limits, and repeated-run/statistical comparability. Do not treat ordinary IID sample bootstrap as automatically valid for correlated questions, repeated trials or trading paths. No trading-profitability or broker-order-safety claims are made.
