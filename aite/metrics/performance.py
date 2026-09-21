"""Performance metrics: latency / TTFT / TPOT / ITL / cache hit / throughput.

时延类指标统一多档位：均值 + p50/p90/p95/p99/max（同一逐请求数据源，
仅聚合口径不同——子类只换 ``aggregation``，compute 复用基类）。

口径说明（与压测工具域 locust/evalscope-perf 对齐）：

- TPOT = (端到端时延 − TTFT) / (completion_tokens − 1)（生成段均摊）
- ITL 档级值 = 逐请求同档统计量的再聚合（jsonl/请求级不保留全量逐 token
  样本的分位近似，如 itl_p95_ms = p95(每请求的 p95)）——各请求到达分布
  同质时近似成立；latency/ttft/tpot 每请求一个样本，档级分位即精确值
- cache_hit_rate = cached_tokens / prompt_tokens（端点回传缓存明细才有，
  未回传 = 不可测 → value None 聚合跳过，宁缺毋错不算 0）
"""

from __future__ import annotations

from typing import ClassVar

from aite.core.aggregator import aggregate_value
from aite.core.interfaces import Metric
from aite.core.registry import register_metric
from aite.core.types import MetricResult


def _unavailable(name: str, reason: str) -> MetricResult:
    """本次不可测：value None（聚合跳过）+ 显式 error，宁缺毋错不算 0。"""
    return MetricResult(metric=name, value=None, error=reason)


@register_metric("ttft_ms")
class TTFT(Metric):
    """Time-to-first-token in ms (streaming targets). Aggregated as mean."""

    name: ClassVar[str] = "ttft_ms"
    higher_is_better: ClassVar[bool] = False

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        value = response.timings.ttft_ms
        if value is None:
            return _unavailable(self.name, "ttft unavailable (non-streaming or failed request)")
        return MetricResult(metric=self.name, value=round(value, 3),
                            raw={"ttft_ms": value},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("ttft_p50_ms")
class TTFTP50(TTFT):
    name: ClassVar[str] = "ttft_p50_ms"
    aggregation: ClassVar[str] = "p50"


@register_metric("ttft_p90_ms")
class TTFTP90(TTFT):
    name: ClassVar[str] = "ttft_p90_ms"
    aggregation: ClassVar[str] = "p90"


@register_metric("ttft_p95_ms")
class TTFTP95(TTFT):
    name: ClassVar[str] = "ttft_p95_ms"
    aggregation: ClassVar[str] = "p95"


@register_metric("ttft_p99_ms")
class TTFTP99(TTFT):
    name: ClassVar[str] = "ttft_p99_ms"
    aggregation: ClassVar[str] = "p99"


@register_metric("ttft_max_ms")
class TTFTMax(TTFT):
    name: ClassVar[str] = "ttft_max_ms"
    aggregation: ClassVar[str] = "max"


@register_metric("latency_ms")
class Latency(Metric):
    """End-to-end latency in ms (summary = p95 by default)."""

    name: ClassVar[str] = "latency_ms"
    higher_is_better: ClassVar[bool] = False
    aggregation: ClassVar[str] = "p95"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        value = response.timings.total_ms
        if value is None:
            return _unavailable(self.name, "no timing recorded")
        return MetricResult(metric=self.name, value=round(value, 3),
                            raw={"total_ms": value},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("latency_avg_ms")
class LatencyAvg(Latency):
    name: ClassVar[str] = "latency_avg_ms"
    aggregation: ClassVar[str] = "mean"


@register_metric("latency_p50_ms")
class LatencyP50(Latency):
    name: ClassVar[str] = "latency_p50_ms"
    aggregation: ClassVar[str] = "p50"


@register_metric("latency_p90_ms")
class LatencyP90(Latency):
    name: ClassVar[str] = "latency_p90_ms"
    aggregation: ClassVar[str] = "p90"


@register_metric("latency_p95_ms")
class LatencyP95(Latency):
    name: ClassVar[str] = "latency_p95_ms"
    aggregation: ClassVar[str] = "p95"


@register_metric("latency_p99_ms")
class LatencyP99(Latency):
    name: ClassVar[str] = "latency_p99_ms"
    aggregation: ClassVar[str] = "p99"


@register_metric("latency_max_ms")
class LatencyMax(Latency):
    name: ClassVar[str] = "latency_max_ms"
    aggregation: ClassVar[str] = "max"


@register_metric("tpot_ms")
class TPOT(Metric):
    """Time-per-output-token in ms: (端到端 − TTFT) / (completion_tokens − 1)。

    需流式计时与 ≥2 个输出 token；子类只换聚合档位（p50/p90/p95/p99/max）。
    """

    name: ClassVar[str] = "tpot_ms"
    higher_is_better: ClassVar[bool] = False

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        timings = response.timings
        tokens = response.usage.completion_tokens if response.usage else None
        if not tokens and isinstance(response.raw, dict):
            # usage 缺失时以内容块数近似（流式主流端点 1 chunk ≈ 1 token）
            chunks = response.raw.get("chunks")
            if isinstance(chunks, int) and chunks > 0:
                tokens = chunks
        if timings.total_ms is None or not timings.ttft_ms or not tokens or tokens < 2:
            return _unavailable(self.name, "tpot unavailable (needs streaming timing"
                                " and >=2 completion tokens)")
        value = max(timings.total_ms - timings.ttft_ms, 1e-6) / (tokens - 1)
        return MetricResult(metric=self.name, value=round(value, 3),
                            raw={"tpot_ms": value, "completion_tokens": tokens},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("tpot_p50_ms")
class TPOTP50(TPOT):
    name: ClassVar[str] = "tpot_p50_ms"
    aggregation: ClassVar[str] = "p50"


@register_metric("tpot_p90_ms")
class TPOTP90(TPOT):
    name: ClassVar[str] = "tpot_p90_ms"
    aggregation: ClassVar[str] = "p90"


@register_metric("tpot_p95_ms")
class TPOTP95(TPOT):
    name: ClassVar[str] = "tpot_p95_ms"
    aggregation: ClassVar[str] = "p95"


@register_metric("tpot_p99_ms")
class TPOTP99(TPOT):
    name: ClassVar[str] = "tpot_p99_ms"
    aggregation: ClassVar[str] = "p99"


@register_metric("tpot_max_ms")
class TPOTMax(TPOT):
    name: ClassVar[str] = "tpot_max_ms"
    aggregation: ClassVar[str] = "max"


@register_metric("itl_ms")
class ITL(Metric):
    """Inter-token latency in ms（逐 token 到达间隔）。

    每请求产出同档统计量（mean/p50/...），档级值为其再聚合——如 itl_p95_ms
    = p95(每请求的 ITL p95)，与压测工具域 jsonl 口径一致。需流式。
    """

    name: ClassVar[str] = "itl_ms"
    higher_is_better: ClassVar[bool] = False
    stat: ClassVar[str] = "mean"
    aggregation: ClassVar[str] = "mean"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        samples = response.timings.itl_samples_ms
        if not samples:
            return _unavailable(self.name, "itl unavailable (non-streaming or failed request)")
        value = aggregate_value([float(v) for v in samples], self.stat)
        return MetricResult(metric=self.name, value=round(value, 3),
                            raw={"itl_samples": len(samples)},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("itl_p50_ms")
class ITLP50(ITL):
    name: ClassVar[str] = "itl_p50_ms"
    stat: ClassVar[str] = "p50"
    aggregation: ClassVar[str] = "p50"


@register_metric("itl_p90_ms")
class ITLP90(ITL):
    name: ClassVar[str] = "itl_p90_ms"
    stat: ClassVar[str] = "p90"
    aggregation: ClassVar[str] = "p90"


@register_metric("itl_p95_ms")
class ITLP95(ITL):
    name: ClassVar[str] = "itl_p95_ms"
    stat: ClassVar[str] = "p95"
    aggregation: ClassVar[str] = "p95"


@register_metric("itl_p99_ms")
class ITLP99(ITL):
    name: ClassVar[str] = "itl_p99_ms"
    stat: ClassVar[str] = "p99"
    aggregation: ClassVar[str] = "p99"


@register_metric("itl_max_ms")
class ITLMax(ITL):
    name: ClassVar[str] = "itl_max_ms"
    stat: ClassVar[str] = "max"
    aggregation: ClassVar[str] = "max"


@register_metric("cache_hit_rate")
class CacheHitRate(Metric):
    """Prompt 缓存命中率 = cached_tokens / prompt_tokens。

    端点回传缓存明细（OpenAI/vLLM prompt_tokens_details、Anthropic
    cache_read_input_tokens、DeepSeek prompt_cache_hit_tokens）才有值；
    未回传 = 不可测（value None 聚合跳过），不算误导性的 0。
    """

    name: ClassVar[str] = "cache_hit_rate"
    higher_is_better: ClassVar[bool] = True

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        usage = response.usage
        cached = usage.cached_tokens if usage else None
        prompt = usage.prompt_tokens if usage else 0
        if cached is None or not prompt:
            return _unavailable(self.name, "cache_hit_rate unavailable"
                                " (endpoint does not report cached tokens)")
        rate = cached / prompt
        return MetricResult(metric=self.name, value=round(rate, 6),
                            raw={"cached_tokens": cached, "prompt_tokens": prompt},
                            passed=self.judge_passed(rate) if self.threshold is not None else None)


@register_metric("tokens_per_sec")
class TokensPerSec(Metric):
    name: ClassVar[str] = "tokens_per_sec"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        tps = response.timings.tokens_per_sec
        if tps is None:
            return _unavailable(self.name, "tokens_per_sec unavailable")
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
