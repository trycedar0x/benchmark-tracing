"""Bundled offline benchmarks and a deterministic mock model provider.

These let the whole product run without network access or API keys:

    everyeval run toy-arith --model mock/strong --model mock/weak

Mock models answer correctly at a fixed, model-specific rate decided by a hash
of the model name and question, so results are reproducible across runs.
"""

from __future__ import annotations

import ast
import hashlib
import operator
import random
import re
from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessage,
    ChatMessageTool,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    ModelUsage,
    modelapi,
)
from inspect_ai.scorer import match
from inspect_ai.solver import generate, system_message, use_tools
from inspect_ai.tool import Tool, ToolChoice, ToolInfo, tool

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}
_QUESTION = re.compile(r"What is (-?\d+) ([+\-*]) (-?\d+)\?")


def _questions(seed: int, count: int, low: int, high: int) -> list[tuple[str, int]]:
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        a, b = rng.randint(low, high), rng.randint(low, high)
        op = rng.choice(["+", "-", "*"])
        value = {"+": a + b, "-": a - b, "*": a * b}[op]
        out.append((f"What is {a} {op} {b}?", value))
    return out


@task(name="toy_arith")
def toy_arith() -> Task:
    samples = [
        Sample(id=f"arith-{i:03d}", input=f"{q} Reply with only the number.", target=str(v))
        for i, (q, v) in enumerate(_questions(seed=7, count=40, low=2, high=99))
    ]
    return Task(dataset=samples, solver=generate(), scorer=match(location="end", numeric=True))


@tool
def calculator() -> Tool:
    async def execute(expression: str) -> str:
        """Evaluate an integer arithmetic expression using +, - and *.

        Args:
            expression: The expression to evaluate, for example "12 * 34".
        """
        return str(_safe_eval(expression))

    return execute


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


@task(name="toy_tools")
def toy_tools() -> Task:
    samples = [
        Sample(id=f"tools-{i:03d}", input=f"{q} Use the calculator, then reply with only the number.", target=str(v))
        for i, (q, v) in enumerate(_questions(seed=11, count=30, low=100, high=9999))
    ]
    return Task(
        dataset=samples,
        solver=[system_message("You are a careful assistant with a calculator."), use_tools(calculator()), generate()],
        scorer=match(location="end", numeric=True),
    )


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
class EveryEvalMock(ModelAPI):
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
