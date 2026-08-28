"""Pairwise judge with position-bias debiasing (both orders, average win rate)."""

from __future__ import annotations

from typing import ClassVar

from aite.core.interfaces import Judge
from aite.core.registry import register_judge
from aite.core.types import MetricResult
from aite.metrics.judge.base import SYSTEM_PROMPT, _reference_text
from aite.metrics.llm_based import parse_json_loose

PROMPT = """## Instruction
{question}

## Assistant A's answer
{answer_a}

## Assistant B's answer
{answer_b}

{reference_block}
Which answer is better overall? Output JSON only: {{"winner": "A" | "B" | "tie", "reasoning": "<brief>"}}"""


@register_judge("pairwise")
class PairwiseJudge(Judge):
    name: ClassVar[str] = "pairwise"
    prompt_version = "pairwise-v1"

    def __init__(self, threshold: float | None = None, weight: float = 1.0,
                 model: str | None = None, **params) -> None:
        super().__init__(threshold=threshold, weight=weight, **params)
        self.model_alias = model

    async def judge(self, *, case, response, baseline=None, ctx) -> MetricResult:
        if response.error:
            return MetricResult(metric=self.name, value=0.0, passed=False,
                                error=f"target error: {response.error[:200]}")
        baseline_text = baseline.output_text if baseline is not None and baseline.ok else \
            _reference_text(case)
        question = case.input if isinstance(case.input, str) else str(case.input)
        reference_block = ""
        if case.expected is not None and not isinstance(case.expected, (list, dict)):
            reference_block = f"## Reference answer\n{case.expected}\n"

        # position-debiased double evaluation
        winner_ab = await self._ask(ctx, question, response.output_text, baseline_text, reference_block)
        winner_ba = await self._ask(ctx, question, baseline_text, response.output_text, reference_block)

        score = 0.0
        detail = {"order_ab": winner_ab, "order_ba": winner_ba}
        if winner_ab == "A" and winner_ba == "B":
            score = 1.0          # consistent win for the candidate
        elif winner_ab == "B" and winner_ba == "A":
            score = 0.0          # consistent loss
        else:
            score = 0.5          # tie or position bias detected
            detail["position_bias"] = winner_ab == winner_ba
        passed = None
        if self.threshold is not None:   # threshold on win-rate scale (0~1)
            passed = score >= self.threshold
        return MetricResult(metric=self.name, value=score, raw=detail, passed=passed)

    async def _ask(self, ctx, question, a: str, b: str, reference_block: str) -> str:
        user = PROMPT.format(question=question, answer_a=a, answer_b=b,
                             reference_block=reference_block)
        try:
            text = await ctx.llm_complete(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": user}],
                model=self.model_alias,
                temperature=0.0, max_tokens=400, json_mode=True,
            )
            data = parse_json_loose(text)
            winner = str((data or {}).get("winner", "tie")).strip().upper()
            return winner if winner in ("A", "B", "TIE") else "tie"
        except Exception:  # noqa: BLE001
            return "tie"
