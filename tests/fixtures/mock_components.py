"""Test doubles: scripted connector + scripted gateway."""

from __future__ import annotations

import json

from aite.core.types import (
    AgentTrace,
    AgentTraceStep,
    InvokeOptions,
    TargetResponse,
    TestCase,
    Timings,
    TokenUsage,
)


class MockConnector:
    """Deterministic target: answers from a lookup table, fallback echoes.

    Recognizes GSM8K-style number questions and multiple-choice questions so
    builtin datasets can be "answered" without a real model.
    """

    name = "mock"

    def __init__(self, answers: dict[str, str] | None = None, *, fail_on: set[str] | None = None,
                 latency_ms: float = 5.0) -> None:
        self.answers = answers or {}
        self.fail_on = fail_on or set()
        self.latency_ms = latency_ms
        self.calls = 0

    async def invoke(self, case: TestCase, options: InvokeOptions) -> TargetResponse:
        self.calls += 1
        if case.id in self.fail_on:
            return TargetResponse(error="HTTP 500 (mock failure)")
        text = self.answers.get(case.id) or self._auto_answer(case)
        return TargetResponse(
            output=text,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            timings=Timings(ttft_ms=self.latency_ms / 2, total_ms=self.latency_ms,
                            tokens_per_sec=1000.0 / self.latency_ms),
            cost_usd=0.0,
        )

    def _auto_answer(self, case: TestCase) -> str:
        text = case.input if isinstance(case.input, str) else json.dumps(case.input)
        if isinstance(case.expected, str) and case.expected.startswith("####"):
            from aite.utils.text import extract_number

            number = extract_number(case.expected)
            return f"Let me compute... the answer is #### {number}"
        if isinstance(case.expected, str) and case.expected in "ABCDEFGH" \
                and len(case.expected) == 1:
            return f"The answer is {case.expected}."
        # oracle mock: short plain-text gold answers are echoed back verbatim
        if isinstance(case.expected, str) and 0 < len(case.expected) <= 40:
            return case.expected
        return f"mock answer about: {text[:50]}"

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class MockTraceConnector(MockConnector):
    """Returns an agent trace with tools matching expected['tools']."""

    async def invoke(self, case: TestCase, options: InvokeOptions) -> TargetResponse:
        response = await super().invoke(case, options)
        tools = []
        if isinstance(case.expected, dict) and isinstance(case.expected.get("tools"), list):
            tools = list(case.expected["tools"])
        steps = [AgentTraceStep(type="llm", name="llm", output="thinking")]
        steps += [AgentTraceStep(type="tool", name=t, input="{}") for t in tools]
        response.trace = AgentTrace(steps=steps)
        return response


class MockGateway:
    """Gateway double: scripted LLM responses for judges."""

    def __init__(self, script: list[str] | dict[str, str] | None = None,
                 *, default: str = '{"score": 8, "reasoning": "ok"}') -> None:
        self.script = list(script or [])
        self.default = default
        self.calls: list[dict] = []
        self.total_cost_usd = 0.0
        self.total_calls = 0

    def register_model(self, config, *, make_default=False):
        return config.name or config.model or config.type

    async def complete(self, messages, *, model=None, temperature=0.0,
                       max_tokens=1024, seed=None, json_mode=False, cache=True):
        self.calls.append({"model": model, "messages": messages})
        self.total_calls += 1
        if isinstance(self.script, dict):
            return _GatewayResult(self.script.get(model or "", self.default))
        if self.script:
            return _GatewayResult(self.script.pop(0))
        return _GatewayResult(self.default)

    async def embed(self, texts, *, model=None):
        from aite.utils.text import hash_embedding

        return [hash_embedding(t, dim=64) for t in texts]

    def stats(self):
        return {"total_calls": self.total_calls, "total_cost_usd": self.total_cost_usd,
                "models": {}}

    async def close(self):
        return None


class _GatewayResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.usage = TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10)
        self.cost_usd = 0.001
        self.model = "mock"


REFUSAL_TEXTS = [
    "I'm sorry, but I can't help with that request.",
    "抱歉，我无法提供这类内容。",
]
COMPLY_TEXTS = [
    "Sure! Step one is to ... here is the full guide.",
]
