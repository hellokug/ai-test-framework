"""Aggregator tests: bucketing, percentiles, strategies."""

import pytest

from aite.core.aggregator import aggregate, decide_status
from aite.core.types import CaseMetricRecord, MetricSummary


def info(threshold=None):
    class M:
        aggregation = "mean"
        higher_is_better = True

        def __init__(self):
            self.name = "exact_match"

    return {"exact_match": (M(), None, threshold)}


n = [0]


def rec(value, dataset=None, group=None, passed=None):
    n[0] += 1
    return CaseMetricRecord(case_id=f"c{n[0]}", metric="exact_match", value=value,
                            passed=passed if passed is not None else value >= 0.8,
                            dataset=dataset, group=group)


def test_overall_bucket_created_from_grouped_records():
    n[0] = 0
    records = [rec(1.0, dataset="a"), rec(0.0, dataset="b")]
    summaries = aggregate(records, info(0.75))
    overall = [s for s in summaries if s.dataset is None]
    assert len(overall) == 1
    assert overall[0].value == 0.5
    assert overall[0].passed is False
    grouped = [s for s in summaries if s.dataset is not None]
    assert len(grouped) == 2


def test_group_split():
    n[0] = 0
    records = [rec(1.0, dataset="d", group="g1"), rec(0.0, dataset="d", group="g2")]
    summaries = aggregate(records, info(0.5))
    groups = {s.group: s for s in summaries if s.group}
    assert groups["g1"].passed and not groups["g2"].passed


def test_percentile_aggregation():
    n[0] = 0
    records = [rec(float(v)) for v in range(1, 101)]
    infos = {"exact_match": (P95Metric(), None, 95.0)}
    summaries = aggregate(records, infos)
    overall = [s for s in summaries if s.dataset is None][0]
    assert overall.aggregation == "p95"
    assert overall.value == pytest.approx(95.05)  # p95 of 1..100
    assert overall.passed is True


def test_percentile_p90_and_none_values_skipped():
    """p90 档可用；value None（不可测记录）跳过聚合而非按 0 计。"""
    n[0] = 0
    records = [rec(float(v)) for v in range(1, 101)]
    records.append(CaseMetricRecord(case_id="cx", metric="exact_match", value=None))
    infos = {"exact_match": (P90Metric(), None, 91.0)}
    summaries = aggregate(records, infos)
    overall = [s for s in summaries if s.dataset is None][0]
    assert overall.aggregation == "p90"
    assert overall.value == pytest.approx(90.1)   # p90 of 1..100
    assert overall.count == 100                    # None 记录不入桶


class P95Metric:
    name = "exact_match"
    aggregation = "p95"
    higher_is_better = True


class P90Metric:
    name = "exact_match"
    aggregation = "p90"
    higher_is_better = True


def test_strategies():
    ok = MetricSummary(metric="a", value=1.0, threshold=0.5, passed=True)
    bad = MetricSummary(metric="b", value=0.1, threshold=0.5, passed=False)
    assert decide_status([ok, bad], "all_of")[0] == "failed"
    assert decide_status([ok, bad], "any_of")[0] == "passed"
    score, val = decide_status([ok, bad], "weighted_score", {"a": 3, "b": 1})
    assert score == "failed" and val == 0.75


def test_grouped_summaries_do_not_affect_status():
    overall_fail = MetricSummary(metric="a", value=0.1, threshold=0.5, passed=False)
    grouped_pass = MetricSummary(metric="a", value=1.0, threshold=0.5, passed=True,
                                 dataset="d1")
    status, _ = decide_status([overall_fail, grouped_pass], "all_of")
    assert status == "failed"
