"""Aggregation: per-case metric values -> run-level summaries (metric x group).

Groups come from dataset label and (optional) ``group_by`` metadata key.
Percentile aggregations (p50/p95/p99) use ``statistics`` on per-case values.
"""

from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING, Any

from aite.core.types import CaseMetricRecord, MetricSummary

if TYPE_CHECKING:
    from aite.core.interfaces import Judge, Metric
    from aite.core.types import RunReport


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return ordered[int(k)]
    return ordered[f] * (c - k) + ordered[c] * (k - f)


def aggregate_value(values: list[float], aggregation: str) -> float:
    if not values:
        return 0.0
    if aggregation in ("p50", "p90", "p95", "p99"):
        return _percentile(values, {"p50": 0.50, "p90": 0.90,
                                    "p95": 0.95, "p99": 0.99}[aggregation])
    if aggregation == "max":
        return max(values)
    if aggregation == "min":
        return min(values)
    if aggregation == "sum":
        return sum(values)
    return statistics.fmean(values)  # mean | rate (mean of 0/1 values)


def aggregate(
    records: list[CaseMetricRecord],
    metric_infos: dict[str, tuple[Metric | None, Judge | None, float | None]],
) -> list[MetricSummary]:
    """Build MetricSummary list.

    Args:
        records: per-case metric records (carrying dataset/group labels).
        metric_infos: metric name -> (metric, judge, threshold). threshold may come
            from judge config when the metric is a judge.
    """
    # buckets: (metric, dataset, group) -> values / passes
    buckets: dict[tuple[str, str | None, str | None], dict[str, Any]] = {}
    for rec in records:
        if rec.value is None:
            continue
        info = metric_infos.get(rec.metric)
        if info is None:
            continue
        metric, judge, threshold = info
        impl = metric or judge
        agg = getattr(metric, "aggregation", "mean") if metric else "mean"
        higher = getattr(impl, "higher_is_better", True) if impl else True
        key = (rec.metric, rec.dataset, rec.group)
        bucket = buckets.setdefault(
            key, {"values": [], "passed": [], "threshold": threshold,
                  "agg": agg, "higher": higher}
        )
        bucket["values"].append(rec.value)
        if rec.passed is not None:
            bucket["passed"].append(bool(rec.passed))
        if bucket["threshold"] is None:
            bucket["threshold"] = threshold
        # fold into the overall bucket as well (dataset=None, group=None)；
        # 无数据集标签的记录其分桶即 overall——跳过折叠避免重复入桶（count 翻倍）
        overall_key = (rec.metric, None, None)
        if key == overall_key:
            continue
        overall = buckets.setdefault(
            overall_key,
            {"values": [], "passed": [], "threshold": threshold,
             "agg": agg, "higher": higher},
        )
        overall["values"].append(rec.value)
        if rec.passed is not None:
            overall["passed"].append(bool(rec.passed))
        if overall["threshold"] is None:
            overall["threshold"] = threshold

    summaries: list[MetricSummary] = []
    for (metric_name, dataset, group), bucket in buckets.items():
        values = bucket["values"]
        threshold = bucket["threshold"]
        higher = bucket["higher"]
        agg_value = aggregate_value(values, bucket["agg"])
        passed: bool | None = None
        if threshold is not None:
            passed = agg_value >= threshold if higher else agg_value <= threshold
        pass_rate = (sum(bucket["passed"]) / len(bucket["passed"])) if bucket["passed"] else None
        summaries.append(
            MetricSummary(
                metric=metric_name, dataset=dataset, group=group,
                aggregation=bucket["agg"], value=round(agg_value, 6),
                threshold=threshold, higher_is_better=higher, passed=passed,
                count=len(values), pass_rate=None if pass_rate is None else round(pass_rate, 6),
                values_min=round(min(values), 6), values_max=round(max(values), 6),
            )
        )
    # overall (dataset=None, group=None) first, then grouped, stable order
    summaries.sort(key=lambda s: (s.dataset is not None, s.group is not None, s.metric))
    return summaries


def decide_status(
    summaries: list[MetricSummary],
    strategy: str,
    weights: dict[str, float] | None = None,
) -> tuple[str, float]:
    """Apply pass_strategy over OVERALL summaries only (dataset/group = None)."""
    judged = [s for s in summaries
              if s.passed is not None and s.dataset is None and s.group is None]
    if not judged:
        return "passed", 0.0
    if strategy == "any_of":
        ok = any(s.passed for s in judged)
    elif strategy == "weighted_score":
        w = weights or {}
        total_w = sum(w.get(s.metric, 1.0) for s in judged)
        score = sum(
            w.get(s.metric, 1.0) * (s.pass_rate if s.pass_rate is not None else (1.0 if s.passed else 0.0))
            for s in judged
        ) / max(total_w, 1e-9)
        return ("passed" if score >= 1.0 else "failed"), round(score, 6)
    else:  # all_of
        ok = all(s.passed for s in judged)
    return ("passed" if ok else "failed"), (1.0 if ok else 0.0)


def attach_summaries(report: RunReport, summaries: list[MetricSummary],
                     strategy: str, weights: dict[str, float] | None) -> None:
    report.summaries = summaries
    status, score = decide_status(summaries, strategy, weights)
    report.status = status
    report.details["overall_score"] = score


def execution_status(report: RunReport) -> str:
    """执行健康度（与评估结论正交的第二维度）。

    success = 全部 case 调用成功；partial = 部分失败；failed = 全部失败；
    unknown = 无 case。「评估结论 failed 但执行 success」是最需要显式区分的
    场景——指标低分是模型/判官问题，不是框架没跑起来。
    """
    if report.total_cases <= 0:
        return "unknown"
    if report.error_cases == 0:
        return "success"
    return "failed" if report.error_cases >= report.total_cases else "partial"
