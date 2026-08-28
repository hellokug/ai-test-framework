"""Multi-judge ensemble: N score judges, mean + agreement reporting."""

from __future__ import annotations

import statistics
from typing import ClassVar

from aite.core.interfaces import Judge
from aite.core.registry import register_judge
from aite.core.types import MetricResult
from aite.metrics.judge.base import ScoreJudge


@register_judge("multi")
class MultiJudge(Judge):
    name: ClassVar[str] = "multi"
    prompt_version = "multi-v1"

    def __init__(self, threshold: float | None = None, weight: float = 1.0,
                 rubric: str | None = None, models: list[str] | None = None, **params) -> None:
        super().__init__(threshold=threshold, weight=weight, **params)
        self.rubric = rubric
        self.model_aliases = list(models or [])
        self.aggregation = str(self.params.get("aggregation", "mean"))  # mean | median
        self.scale_max = float(self.params.get("scale_max", 10.0))

    async def judge(self, *, case, response, baseline=None, ctx) -> MetricResult:
        if not self.model_aliases:
            return MetricResult(metric=self.name, value=0.0,
                                error="multi judge requires >=2 models (models: [...])")
        sub_results: list[float] = []
        errors: list[str] = []
        for alias in self.model_aliases:
            sub = ScoreJudge(threshold=None, rubric=self.rubric, model=alias)
            try:
                res = await sub.judge(case=case, response=response, baseline=baseline, ctx=ctx)
                if res.error:
                    errors.append(f"{alias}: {res.error}")
                else:
                    sub_results.append(res.raw.get("score", res.value * 10))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{alias}: {exc}")
        if not sub_results:
            return MetricResult(metric=self.name, value=0.0, error="; ".join(errors)[:400])
        value_raw = (statistics.median(sub_results) if self.aggregation == "median"
                     else statistics.fmean(sub_results))
        value = value_raw / 10.0
        agreement = 1.0 - (statistics.pstdev(sub_results) / 10.0) if len(sub_results) > 1 else 1.0
        passed = None
        if self.threshold is not None:
            passed = value_raw >= self.threshold
        return MetricResult(
            metric=self.name, value=round(value, 6),
            raw={"scores": sub_results, "agreement": round(agreement, 4),
                 "models": self.model_aliases, "errors": errors},
            passed=passed,
        )
