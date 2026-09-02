"""工具适配层单测（全离线：不 import evalscope、不触网）。"""

import json

import pytest

from aite.tools import parse_tool_suite
from aite.tools.base import ToolRunRequest, get_tool
from aite.tools.evalscope_adapter import EvalscopeAdapter


def make_request(**kw):
    base = dict(
        run_name="t",
        target={"type": "openai_compatible", "base_url": "http://localhost:11434/v1",
                "model": "qwen2.5:3b"},
        datasets=["ceval"],
    )
    base.update(kw)
    return ToolRunRequest(**base)


def write_report(work_dir, filename, payload):
    """filename 可含子目录（no_timestamp 模式为 reports/<model>/<ds>.json）。"""
    path = work_dir / "reports" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestEvalscopeAdapter:
    def test_build_config_maps_target(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        cfg = EvalscopeAdapter().build_config(make_request(limit_per_subset=2))
        assert cfg["api_url"] == "http://localhost:11434/v1/chat/completions"
        assert cfg["api_key"] == "sk-test"
        assert cfg["eval_type"] == "openai_api"
        assert cfg["model"] == "qwen2.5:3b"
        assert cfg["datasets"] == ["ceval"]
        assert cfg["limit"] == 2            # 每科目条数语义直接映射
        assert cfg["no_timestamp"] is True  # 时间戳由 aite 侧目录承担

    def test_build_config_extra_passthrough(self):
        req = make_request(extra={"eval_backend": "OpenCompass"})
        cfg = EvalscopeAdapter().build_config(req)
        assert cfg["eval_backend"] == "OpenCompass"

    def test_build_config_rejects_non_openai_target(self):
        req = make_request(target={"type": "anthropic"})
        with pytest.raises(ValueError, match="openai_compatible"):
            EvalscopeAdapter().build_config(req)

    def test_collect_parses_report_json(self, tmp_path):
        # no_timestamp 模式的实际布局：reports/<model>/<ds>.json
        write_report(tmp_path, "qwen2.5_3b/gsm8k.json", {
            "dataset_name": "gsm8k", "score": 0.6859,
            "metrics": [{"name": "mean_acc", "num": 156, "score": 0.6859}],
            "perf_metrics": {"summary": {"n_samples": 156}},
        })
        req = make_request(work_dir=tmp_path, metric_thresholds={"mean_acc": 0.5})
        result = EvalscopeAdapter().collect(req)
        assert result.raw_score == {"gsm8k": 0.6859}
        row = result.rows[0]
        assert row.dataset == "evalscope:gsm8k"
        assert row.metric == "mean_acc" and row.count == 156
        assert row.threshold == 0.5 and row.passed is True
        assert "gsm8k.json" in result.report_files
        assert result.perf["gsm8k"]["n_samples"] == 156

    def test_collect_no_threshold_passes_none(self, tmp_path):
        write_report(tmp_path, "m@gsm8k.json", {
            "dataset_name": "gsm8k", "score": 0.1,
            "metrics": [{"name": "mean_acc", "num": 3, "score": 0.1}],
        })
        result = EvalscopeAdapter().collect(make_request(work_dir=tmp_path))
        assert result.rows[0].passed is None

    def test_collect_skips_duplicate_dataset(self, tmp_path):
        payload = {"dataset_name": "gsm8k", "score": 0.2,
                   "metrics": [{"name": "mean_acc", "num": 1, "score": 0.2}]}
        write_report(tmp_path, "a@gsm8k.json", payload)
        write_report(tmp_path, "b@gsm8k.json", payload)
        result = EvalscopeAdapter().collect(make_request(work_dir=tmp_path))
        assert len(result.rows) == 1


class TestToolSuiteParsing:
    def test_parse_requires_datasets(self):
        with pytest.raises(ValueError, match="tools.datasets"):
            parse_tool_suite({"engine": "evalscope", "tools": {}})

    def test_unknown_engine(self):
        with pytest.raises(KeyError, match="unknown tool engine"):
            get_tool("no_such_tool")


async def test_tool_suite_overall_row(repo, tmp_path, monkeypatch):
    """工具 run 必须产出 overall 行（dataset=None），CLI 汇总表只显示 overall。"""
    from aite.tools import run_tool_suite
    from aite.tools.base import ToolMetricRow, ToolRunResult
    from aite.tools.evalscope_adapter import EvalscopeAdapter

    async def fake_run(self, req):
        return ToolRunResult(rows=[
            ToolMetricRow(dataset="evalscope:gsm8k", metric="mean_acc",
                          value=1.0, count=2, threshold=0.3, passed=True),
        ])

    monkeypatch.setattr(EvalscopeAdapter, "run", fake_run)
    raw = {"engine": "evalscope", "run_name": "t",
           "target": {"type": "openai_compatible", "base_url": "http://x/v1", "model": "m"},
           "tools": {"datasets": ["gsm8k"], "metric_thresholds": {"mean_acc": 0.3}}}
    report = await run_tool_suite(raw, repo)
    assert report.status == "passed"
    overall = report.summary_for("mean_acc")
    assert overall is not None and overall.dataset is None
    assert overall.value == 1.0 and overall.count == 2
    # 分组行与 overall 行都入库
    assert len(report.summaries) == 2
