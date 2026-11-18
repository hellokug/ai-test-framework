"""evalscope 适配器 collect / 基准依赖预检测试（全离线：不装 evalscope）。

schema v2 报告把指标名放 metrics[n].identity.name（如 accuracy），旧版平铺
name；取错字段会把指标全部记成 score，auto/显式阈值别名双双静默失效。
依赖预检：假 evalscope 包布局（tmp 目录 + PYTHONPATH 注入子进程）验证
requirements.txt 发现与 pip 调用决策，不碰网络。
"""

import json
import sys

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


def fake_evalscope_pkg(tmp_path, reqs: dict[str, str]) -> str:
    """造假 evalscope 包布局：{基准名: requirements.txt 内容}；空串=空文件。

    目录必须字面命名 evalscope（find_spec 按名查找），返回父目录喂 PYTHONPATH。"""
    pkg = tmp_path / "evalscope"
    (pkg / "benchmarks").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for ds, content in reqs.items():
        d = pkg / "benchmarks" / ds
        d.mkdir(parents=True, exist_ok=True)
        (d / "requirements.txt").write_text(content, encoding="utf-8")
    return str(tmp_path)


class TestBenchmarkDepPreflight:
    async def test_no_extra_requirements_is_fast_noop(self, tmp_path):
        # 无 requirements.txt / 空文件：不调 pip，直接报 no extra requirements
        pkg = fake_evalscope_pkg(
            tmp_path, {"gsm8k": "", "bfcl_v3": "# comment only\n\n"})
        seen: list[dict] = []

        def cb(ev):
            seen.append(ev)

        lines = await EvalscopeAdapter()._ensure_benchmark_deps(
            sys.executable, ["gsm8k", "bfcl_v3"], cb,
            env_extra={"PYTHONPATH": pkg})
        assert lines[-1].endswith("no extra requirements")
        assert [ev["type"] for ev in seen] == ["log"] * len(lines)

    async def test_satisfied_requirement_goes_through_pip_idempotently(
            self, tmp_path):
        # requirements.txt 非空 → 真走 pip（pytest 已装、无版本升级 → 不碰网络）
        pkg = fake_evalscope_pkg(tmp_path, {"bfcl_v3": "pytest\n"})
        lines = await EvalscopeAdapter()._ensure_benchmark_deps(
            sys.executable, ["bfcl_v3"], None, env_extra={"PYTHONPATH": pkg})
        assert any("pip install -r" in ln for ln in lines)
        assert lines[-1].endswith("['bfcl_v3']")

    async def test_family_dir_requirements_matched_by_registry_name(
            self, tmp_path):
        # bfcl_v3 注册名 ≠ 目录名（benchmarks/bfcl/，v3/v4 共用）：
        # 祖先候选必须命中，且两个注册名去重后只装一次
        pkg = fake_evalscope_pkg(tmp_path, {"bfcl": "pytest\n"})
        lines = await EvalscopeAdapter()._ensure_benchmark_deps(
            sys.executable, ["bfcl_v3", "bfcl_v4"], None,
            env_extra={"PYTHONPATH": pkg})
        pip_lines = [ln for ln in lines if "pip install -r" in ln]
        assert len(pip_lines) == 1
        assert lines[-1].endswith("['bfcl']")

    async def test_bad_interpreter_surfaces_runtime_error(self, tmp_path):
        import pytest
        with pytest.raises(RuntimeError, match="基准依赖预检失败"):
            await EvalscopeAdapter()._ensure_benchmark_deps(
                str(tmp_path / "nope.exe"), ["bfcl_v3"], None)

    async def test_subprocess_run_preflights_before_spawn(self, tmp_path,
                                                          monkeypatch):
        # _run_subprocess_impl 必须先预检依赖再拉起 RUN_TASK_BOOTSTRAP
        order: list[str] = []
        adapter = EvalscopeAdapter()

        async def fake_ensure(python_exe, datasets, progress_cb=None,
                              env_extra=None):
            order.append(f"ensure:{python_exe}:{datasets}")
            return []

        monkeypatch.setattr(adapter, "_ensure_benchmark_deps", fake_ensure)

        class FakeProc:
            returncode = 0

            def poll(self):
                return 0

        monkeypatch.setattr(
            "aite.tools.evalscope_adapter.subprocess.Popen",
            lambda *a, **k: order.append("popen") or FakeProc())
        req = make_req(tmp_path, dataset="bfcl_v3")
        await adapter._run_subprocess_impl(req, "py310.exe")
        assert order == ["ensure:py310.exe:['bfcl_v3']", "popen"]
