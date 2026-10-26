"""Pinned finance adapters; generic evaluation does not import Inspect or a solver."""

from __future__ import annotations

import importlib.util
import math
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any

from everybench.evaluation.types import AgentResult, BenchmarkCase, GradeResult, TraceEvidence
from everybench.finance.datasets import DATASETS, MAX_DATASET_BYTES, load_dataset, parse_dataset


class FinanceBenchmark:
    """EveryBench finance adaptations, not certified official leaderboard parity."""

    def __init__(
        self,
        name: str,
        *,
        judge_models: list[str],
        extractor_model: str | None = None,
        data_path: str | Path | None = None,
        tolerance: float = 0.01,
    ) -> None:
        if name not in DATASETS:
            raise ValueError("Unknown finance benchmark.")
        if not judge_models or len(set(judge_models)) != len(judge_models):
            raise ValueError("Choose distinct explicit judge model IDs.")
        models = [*judge_models, *([extractor_model] if extractor_model is not None else [])]
        if any(not isinstance(m, str) or not m.strip() or "REPLACE" in m.upper() for m in models):
            raise ValueError("Explicit evaluator model IDs are required.")
        if name == "findeepindicator" and (len(judge_models) != 1 or not extractor_model):
            raise ValueError("FinDeepIndicator requires one judge and an explicit extractor model.")
        if name == "bigfinance-public-50" and extractor_model is not None:
            raise ValueError("BigFinance does not use an extractor model.")
        if not math.isfinite(tolerance) or not 0 <= tolerance <= 0.1:
            raise ValueError("Tolerance must be between 0 and 0.1.")
        self.name = name
        self.requires_trace = name == "bigfinance-public-50"
        self.judge_models = list(judge_models)
        self.extractor_model = extractor_model
        self.tolerance = tolerance
        self.data_path = Path(data_path) if data_path is not None else None
        for model in models:
            provider, separator, model_id = model.partition("/")
            if (
                not separator
                or not model_id
                or provider not in ("openai", "openrouter", "anthropic", "mock", "mockllm")
            ):
                raise ValueError(
                    "External finance supports explicit openai/openrouter/anthropic or registered mock models."
                )
            package = {"openai": "openai", "openrouter": "openai", "anthropic": "anthropic"}.get(provider)
            if package and importlib.util.find_spec(package) is None:
                raise ValueError("Evaluator SDK missing: install everybench[finance].")
            if package == "anthropic" and tuple(int(v) for v in version("anthropic").split(".")[:2]) < (1, 17):
                raise ValueError("External finance requires anthropic>=1.17.0,<2.")
            if package == "anthropic" and version("anthropic").split(".")[0] != "1":
                raise ValueError("External finance requires anthropic>=1.17.0,<2 (Inspect compatibility).")
            if provider == "mock":
                from everybench.bundled import MOCK_MODELS

                if model_id not in MOCK_MODELS:
                    raise ValueError("Unknown registered mock model.")

    async def validate(self) -> None:
        """Resolve clients/configuration without generating anything, before spending an agent call."""
        from everybench.finance.judging import evaluator_model

        for model in dict.fromkeys([*self.judge_models, *([self.extractor_model] if self.extractor_model else [])]):
            try:
                async with evaluator_model(model, "preflight"):
                    pass
            except Exception:
                raise ValueError("Evaluator client configuration unavailable; no agent invocation performed.") from None

    def describe(self) -> dict[str, Any]:
        return {
            "dataset": asdict(DATASETS[self.name]),
            "protocol": "everybench.bigfinance-public-50.trace-rubric-v1"
            if self.requires_trace
            else "everybench.findeepindicator.response-v1",
            "judge_models": self.judge_models,
            "extractor_model": self.extractor_model,
            "tolerance": self.tolerance if not self.requires_trace else None,
            "jury_mode": "single-judge-adaptation" if len(self.judge_models) == 1 else "multi-judge-adaptation",
            "official_parity": False,
        }

    def cases(self) -> list[BenchmarkCase]:
        if self.data_path is None:
            rows = load_dataset(self.name)
        else:
            with self.data_path.open("rb") as stream:
                rows = parse_dataset(stream.read(MAX_DATASET_BYTES + 1), DATASETS[self.name])
        return [
            BenchmarkCase(id=str(row["id"]), input=row["query" if self.requires_trace else "question"], reference=row)
            for row in rows
        ]

    async def grade(self, case: BenchmarkCase, result: AgentResult, evidence: TraceEvidence | None) -> GradeResult:
        if self.requires_trace:
            from everybench.finance.big_finance import evaluate_evidence

            if evidence is None or evidence.state != "complete":
                raise ValueError("BigFinance requires complete visible agent trace evidence.")
            evaluation = await evaluate_evidence(
                result.answer, evidence, case.reference, judge_models=self.judge_models
            )
            score = evaluation["scores"]["rubric"]
            explanation = "Weighted trace rubric; declared EveryBench adaptation, not official parity."
        else:
            from everybench.finance.indicator import evaluate_response

            evaluation = await evaluate_response(
                result.answer,
                case.reference,
                extractor_model=self.extractor_model,
                judge_model=self.judge_models[0],
                tolerance=self.tolerance,
            )
            score = evaluation["scores"]["answer"]
            explanation = "Final-answer numeric accuracy; other response components are reported separately."
        return GradeResult(
            score=score,
            components=evaluation["scores"],
            explanation=explanation,
            protocol=evaluation["protocol"],
            metadata=evaluation,
        )
