"""Performance metrics: latency / TTFT / throughput / error rate."""

from __future__ import annotations

from typing import ClassVar

from aite.core.interfaces import Metric
from aite.core.registry import register_metric
from aite.core.types import MetricResult


@register_metric("ttft_ms")
class TTFT(Metric):
    """Time-to-first-token in ms (streaming targets). Aggregated as mean."""

    name: ClassVar[str] = "ttft_ms"
    higher_is_better: ClassVar[bool] = False

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        value = response.timings.ttft_ms
        if value is None:
            return MetricResult(metric=self.name, value=0.0,
                                error="ttft unavailable (non-streaming or failed request)")
        return MetricResult(metric=self.name, value=round(value, 3),
                            raw={"ttft_ms": value},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("latency_ms")
class Latency(Metric):
    name: ClassVar[str] = "latency_ms"
    higher_is_better: ClassVar[bool] = False
    aggregation: ClassVar[str] = "p95"     # summary = p95 by default

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        value = response.timings.total_ms
        if value is None:
            return MetricResult(metric=self.name, value=0.0, error="no timing recorded")
        return MetricResult(metric=self.name, value=round(value, 3),
                            raw={"total_ms": value},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("latency_p50_ms")
class LatencyP50(Latency):
    name: ClassVar[str] = "latency_p50_ms"
    aggregation: ClassVar[str] = "p50"


@register_metric("latency_p95_ms")
class LatencyP95(Latency):
    name: ClassVar[str] = "latency_p95_ms"
    aggregation: ClassVar[str] = "p95"


@register_metric("latency_p99_ms")
class LatencyP99(Latency):
    name: ClassVar[str] = "latency_p99_ms"
    aggregation: ClassVar[str] = "p99"


@register_metric("tokens_per_sec")
class TokensPerSec(Metric):
    name: ClassVar[str] = "tokens_per_sec"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        tps = response.timings.tokens_per_sec
        if tps is None:
            return MetricResult(metric=self.name, value=0.0, error="tokens_per_sec unavailable")
        # normalize with a soft cap at 500 tps -> 1.0 (keeps value in 0..1)
        value = min(tps / 500.0, 1.0)
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"tokens_per_sec": round(tps, 3), "raw_normalized_from": 500},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("error_rate")
class ErrorRate(Metric):
    """Fraction of failed invocations (network/HTTP errors). Lower is better."""

    name: ClassVar[str] = "error_rate"
    aggregation: ClassVar[str] = "rate"
    higher_is_better: ClassVar[bool] = False

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        failed = 0.0 if response.ok else 1.0
        return MetricResult(metric=self.name, value=failed,
                            raw={"error": (response.error or "")[:300]},
                            passed=self.judge_passed(failed) if self.threshold is not None else None)


@register_metric("cost_usd")
class CostPerCase(Metric):
    """Estimated USD cost of a single invocation (aggregated = mean per case)."""

    name: ClassVar[str] = "cost_usd"
    higher_is_better: ClassVar[bool] = False

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        value = response.cost_usd if response.cost_usd is not None else 0.0
        # soft-normalize: $0.10 per case -> value 1.0
        normalized = min(value / 0.10, 1.0)
        return MetricResult(metric=self.name, value=round(normalized, 6),
                            raw={"cost_usd": value},
                            passed=self.judge_passed(normalized) if self.threshold is not None else None)
