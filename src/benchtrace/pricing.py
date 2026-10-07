"""Model prices (USD per million tokens) and cost estimates."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from benchtrace.config import settings


class Price(BaseModel):
    input: float
    output: float
    as_of: str | None = None
    source: str | None = None

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input + output_tokens * self.output) / 1_000_000


def load_prices() -> dict[str, Price]:
    paths = [Path(str(resources.files("benchtrace") / "pricing.yaml")), settings().home / "pricing.yaml"]
    prices: dict[str, Price] = {}
    for path in paths:
        if path.exists():
            raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
            for model, data in raw.items():
                data = {k: str(v) if k == "as_of" else v for k, v in data.items()}
                prices[model] = Price.model_validate(data)
    return prices


def price_for(model: str) -> Price | None:
    return load_prices().get(model)


def cost_for(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = price_for(model)
    return price.cost(input_tokens, output_tokens) if price else None
