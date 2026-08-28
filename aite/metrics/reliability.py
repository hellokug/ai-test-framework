"""Reliability metrics over repeat/perturbation variants (engine materializes
the derived cases; these metrics consume the whole variant group)."""

from __future__ import annotations

from typing import ClassVar

from aite.core.interfaces import Metric
from aite.core.registry import register_metric
from aite.core.types import MetricResult, TargetResponse, TestCase
from aite.utils.text import normalize_answer, token_f1


def _variants(responses: list[tuple[TestCase, TargetResponse]] | None,
              prefix: str) -> list[TargetResponse]:
    return [r for c, r in (responses or []) if c.variant.startswith(prefix) and r.ok]


@register_metric("repeat_consistency")
class RepeatConsistency(Metric):
    """Equivalence rate of repeat outputs vs the base output (text-level)."""

    name: ClassVar[str] = "repeat_consistency"
    wants_variants: ClassVar[bool] = True

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        repeats = _variants(responses, "repeat")
        if not repeats:
            return MetricResult(metric=self.name, value=0.0, error="no repeat variants (set dataset repeats: N)")
        base = normalize_answer(response.output_text)
        same = sum(1 for r in repeats if normalize_answer(r.output_text) == base)
        value = same / len(repeats)
        return MetricResult(
            metric=self.name, value=round(value, 6),
            raw={"repeats": len(repeats), "equivalent": same},
            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("perturbation_robustness")
class PerturbationRobustness(Metric):
    """Mean similarity (token F1) of perturbed outputs vs base output."""

    name: ClassVar[str] = "perturbation_robustness"
    wants_variants: ClassVar[bool] = True

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        perturbed = _variants(responses, "perturb")
        if not perturbed:
            return MetricResult(metric=self.name, value=0.0,
                                error="no perturbation variants (set dataset perturbations)")
        sims = [token_f1(r.output_text, response.output_text)[2] for r in perturbed]
        value = sum(sims) / len(sims)
        return MetricResult(
            metric=self.name, value=round(value, 6),
            raw={"variants": len(perturbed),
                 "sims": [round(s, 4) for s in sims]},
            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("stability")
class Stability(Metric):
    """Mean pairwise similarity across ALL variants (base+repeats+perturbations)."""

    name: ClassVar[str] = "stability"
    wants_variants: ClassVar[bool] = True

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        outs = [r.output_text for _, r in (responses or []) if r.ok]
        if len(outs) < 2:
            return MetricResult(metric=self.name, value=0.0, error="needs >=2 variant responses")
        sims = []
        for i in range(len(outs)):
            for j in range(i + 1, len(outs)):
                sims.append(token_f1(outs[i], outs[j])[2])
        value = sum(sims) / len(sims)
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"outputs": len(outs), "pairs": len(sims)},
                            passed=self.judge_passed(value) if self.threshold is not None else None)
