"""Explicit, bounded primary-score selection; never turn unsupported values into zero."""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class ScoreSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scorer: str | None = None
    key: str | None = None
    minimum: float = 0.0
    maximum: float = 1.0
    direction: str = "maximize"

    @model_validator(mode="after")
    def valid_range(self) -> ScoreSpec:
        if not math.isfinite(self.minimum) or not math.isfinite(self.maximum) or self.minimum >= self.maximum:
            raise ValueError("Score bounds must be finite and minimum < maximum.")
        if self.direction not in ("maximize", "minimize"):
            raise ValueError("Score direction must be maximize or minimize.")
        return self


def numeric_score(value: Any) -> float:
    """Inspect's standard categorical scores plus finite numbers; reject dicts and unknown strings."""
    if isinstance(value, str):
        categories = {"C": 1.0, "I": 0.0, "P": 0.5, "N": 0.0}
        if value in categories:
            return categories[value]
        try:
            value = float(value)
        except ValueError as ex:
            raise ValueError("Unrecognized categorical score.") from ex
    if not isinstance(value, int | float):
        raise ValueError("Score must be a finite number or C/I/P/N.")
    try:
        number = float(value)
    except (OverflowError, ValueError) as ex:
        raise ValueError("Score is not representable as a finite number.") from ex
    if not math.isfinite(number):
        raise ValueError("Score must be finite.")
    return number


def normalized_score(value: Any, spec: ScoreSpec | None = None) -> float:
    spec = spec or ScoreSpec()
    if spec.key is not None:
        if not isinstance(value, dict) or spec.key not in value:
            raise ValueError(f"Primary score key {spec.key!r} is missing.")
        value = value[spec.key]
    number = numeric_score(value)
    if not spec.minimum <= number <= spec.maximum:
        raise ValueError("Score is outside its declared bounds.")
    fraction = (number - spec.minimum) / (spec.maximum - spec.minimum)
    return 1.0 - fraction if spec.direction == "minimize" else fraction


def select_score(sample: Any, spec: ScoreSpec | None = None) -> tuple[str | None, Any]:
    scores = sample.scores or {}
    if not scores:
        return None, None
    if spec and spec.scorer:
        if spec.scorer not in scores:
            raise ValueError(f"Primary scorer {spec.scorer!r} is missing.")
        return spec.scorer, scores[spec.scorer]
    return next(iter(scores.items()))
