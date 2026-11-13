"""evalscope 适配器 collect 测试（全离线：报告文件契约夹具，不装 evalscope）。

schema v2 报告把指标名放 metrics[n].identity.name（如 accuracy），旧版平铺
name；取错字段会把指标全部记成 score，auto/显式阈值别名双双静默失效。
"""

import json

from aite.tools.base import ToolRunRequest
from aite.tools.evalscope_adapter import EvalscopeAdapter


def make_req(tmp_path, dataset="gpqa_diamond", thresholds=None, auto=False):
    return ToolRunRequest(
        run_name="t", target={}, datasets=[dataset], work_dir=tmp_path,
        metric_thresholds=thresholds or {}, auto_thresholds=auto)


def write_report(tmp_path, payload, model="deepseek-chat", ds="gpqa_diamond"):
    d = tmp_path / "reports" / model
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{ds}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def v2_report(metric_name="accuracy", score=0.8, num=5, ds="gpqa_diamond"):
    """2026-11 实测 gpqa_diamond 报告形态：schema_version 2 + identity.name。"""
    return {
        "schema_version": 2, "dataset_name": ds, "model_name": "deepseek-chat",
        "score": score,
        "metrics": [{"identity": {"name": metric_name, "aggregation": "mean"},
                     "num": num, "score": score}],
        "perf_metrics": {"summary": {"n_samples": num}},
    }


class TestCollectMetricName:
    def test_v2_identity_name_not_degraded_to_score(self, tmp_path):
        write_report(tmp_path, v2_report())
        out = EvalscopeAdapter().collect(make_req(tmp_path))
        assert [r.metric for r in out.rows] == ["accuracy"]
        assert out.rows[0].value == 0.8 and out.rows[0].count == 5
        assert out.raw_score["gpqa_diamond"] == 0.8

    def test_legacy_flat_name_still_works(self, tmp_path):
        write_report(tmp_path, {"dataset_name": "gsm8k", "score": 0.9,
                                "metrics": [{"name": "mean_acc", "num": 4, "score": 0.9}]})
        out = EvalscopeAdapter().collect(make_req(tmp_path))
        assert [r.metric for r in out.rows] == ["mean_acc"]

    def test_nameless_aggregate_row_falls_back_to_score(self, tmp_path):
        write_report(tmp_path, {"dataset_name": "swe_bench",
                                "metrics": [{"num": 2, "score": 0.5}]})
        out = EvalscopeAdapter().collect(make_req(tmp_path, dataset="swe_bench"))
        assert [r.metric for r in out.rows] == ["score"]
        assert out.raw_score["swe_bench"] == 0.5


class TestThresholdResolution:
    def test_explicit_threshold_via_accuracy_alias(self, tmp_path):
        # suite 阈值按 mean_acc 写，v2 报告报 accuracy → 别名链必须接通
        write_report(tmp_path, v2_report(ds="mmlu"))
        req = make_req(tmp_path, dataset="mmlu", thresholds={"mean_acc": 0.3})
        row = EvalscopeAdapter().collect(req).rows[0]
        assert row.threshold == 0.3 and row.passed is True

    def test_auto_threshold_for_recommended_benchmark(self, tmp_path):
        write_report(tmp_path, v2_report(ds="mmlu"))
        req = make_req(tmp_path, dataset="mmlu", auto=True)
        row = EvalscopeAdapter().collect(req).rows[0]
        assert row.threshold == 0.25 and row.passed is True

    def test_no_recommended_threshold_stays_unjudged(self, tmp_path):
        # gpqa 无客观随机基线：auto 不给默认阈值（避免误导），只报告值
        write_report(tmp_path, v2_report())
        row = EvalscopeAdapter().collect(make_req(tmp_path, auto=True)).rows[0]
        assert row.threshold is None and row.passed is None
