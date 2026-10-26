"""Experimental finance preflight and accounting; no guessed all-inclusive budget claims."""

from __future__ import annotations

from typing import Any

from everybench.pricing import cost_for

TASKS = {"everybench/finance_indicator", "everybench/finance_big_public50"}


def is_finance(entry: Any) -> bool:
    return entry.task in TASKS


def evaluator_models(entry: Any) -> list[str]:
    if not is_finance(entry):
        return []
    args = entry.task_args
    if entry.task.endswith("finance_indicator"):
        models = [args.get("judge_model"), args.get("extractor_model") or args.get("judge_model")]
    else:
        models = args.get("judge_models")
    if not isinstance(models, list) or not models or any(not isinstance(m, str) or not m.strip() for m in models):
        raise ValueError("Finance task_args must explicitly name every judge/extractor model.")
    if any("REPLACE" in m.upper() for m in models):
        raise ValueError("Replace all finance evaluator placeholders with explicit model snapshots.")
    return list(dict.fromkeys(models))


def finance_metrics(log: Any) -> dict[str, Any]:
    per_model = {}
    known_costs = []
    for model, usage in log.stats.model_usage.items():
        cost = cost_for(model, usage.input_tokens, usage.output_tokens)
        known_costs.append(cost)
        per_model[model] = {**usage.model_dump(mode="json"), "estimated_cost_usd": cost}
    components: dict[str, list[float]] = {}
    for sample in log.samples or []:
        for score in (sample.scores or {}).values():
            metadata = score.metadata or {}
            if not str(metadata.get("protocol", "")).startswith("everybench."):
                continue
            for key, value in metadata.get("scores", {}).items():
                if isinstance(value, int | float):
                    components.setdefault(key, []).append(float(value))
    return {
        "model_usage": per_model,
        "role_usage": {name: usage.model_dump(mode="json") for name, usage in log.stats.role_usage.items()},
        "llm_cost_usd": sum(known_costs) if known_costs and all(c is not None for c in known_costs) else None,
        "search_and_data_cost_usd": None,
        "cost_scope": "LLM token estimates only; external search/data fees are not included or capped.",
        "components": {
            key: {"mean": sum(values) / len(values), "n": len(values)} for key, values in components.items()
        },
    }
