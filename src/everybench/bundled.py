"""Deterministic mock evaluator provider for offline user-agent grading tests."""

from __future__ import annotations

import ast
import hashlib
import operator
import re
from typing import Any

from inspect_ai.model import (
    ChatMessage,
    ChatMessageTool,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    ModelUsage,
    modelapi,
)
from inspect_ai.tool import ToolChoice, ToolInfo

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}
_QUESTION = re.compile(r"What is (-?\d+) ([+\-*]) (-?\d+)\?")


def _safe_eval(expression: str) -> int:
    def walk(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -walk(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.left), walk(node.right))
        raise ValueError(f"Unsupported expression: {expression}")

    return walk(ast.parse(expression, mode="eval"))


# Accuracy, error rate and resolved-model behaviour for each mock model.
MOCK_MODELS: dict[str, dict[str, Any]] = {
    "perfect": {"accuracy": 1.0, "error_rate": 0.0},
    "strong": {"accuracy": 0.9, "error_rate": 0.0},
    "weak": {"accuracy": 0.6, "error_rate": 0.0},
    "flaky": {"accuracy": 0.85, "error_rate": 0.1},
    # Resolves to a different backend revision every 7th call, to exercise identity checks.
    "drifty": {"accuracy": 0.9, "error_rate": 0.0, "drift_every": 7},
}


def _unit(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class MockError(RuntimeError):
    pass


@modelapi(name="mock")
class EveryBenchMock(ModelAPI):
    def __init__(
        self,
        model_name: str,
        base_url: str | None = None,
        api_key: str | None = None,
        config: GenerateConfig = GenerateConfig(),
        **model_args: Any,
    ) -> None:
        super().__init__(model_name, base_url, api_key, [], config)
        if model_name not in MOCK_MODELS:
            raise ValueError(f"Unknown mock model mock/{model_name}. Choose from: {', '.join(MOCK_MODELS)}")
        self.profile = MOCK_MODELS[model_name]
        self.calls = 0

    def max_connections(self) -> int:
        return 32

    def should_retry(self, ex: BaseException) -> bool:
        return False

    async def generate(
        self, input: list[ChatMessage], tools: list[ToolInfo], tool_choice: ToolChoice, config: GenerateConfig
    ) -> ModelOutput:
        self.calls += 1
        name = self.model_name
        prompt = "\n".join(m.text for m in input)
        found = _QUESTION.search(prompt)
        question = found.group(0) if found else prompt[-200:]
        revision = "2026-01"
        drift = self.profile.get("drift_every")
        if drift and self.calls % drift == 0:
            revision = "2026-02"
        resolved = f"mock-{name}-{revision}"

        if _unit(name, "error", question) < self.profile["error_rate"]:
            raise MockError(f"mock/{name}: simulated provider error")

        tool_results = [m for m in input if isinstance(m, ChatMessageTool)]
        if tools and found and not tool_results:
            a, op, b = found.group(1), found.group(2), found.group(3)
            output = ModelOutput.for_tool_call(
                model=resolved, tool_name="calculator", tool_arguments={"expression": f"{a} {op} {b}"}
            )
        else:
            if tool_results:
                value = int(tool_results[-1].text)
            elif found:
                value = _safe_eval(f"{found.group(1)} {found.group(2)} {found.group(3)}")
            else:
                value = 0
            if _unit(name, "answer", question) >= self.profile["accuracy"]:
                value += 1 + int(_unit(name, "offset", question) * 9)
            output = ModelOutput.from_content(model=resolved, content=f"The answer is {value}")
        output.usage = ModelUsage(
            input_tokens=_tokens(prompt),
            output_tokens=_tokens(output.completion) + 8,
            total_tokens=_tokens(prompt) + _tokens(output.completion) + 8,
        )
        return output
