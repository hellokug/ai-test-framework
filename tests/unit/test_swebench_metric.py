"""swe_resolution 指标：补丁是否让仓库测试由败转胜（官方 harness 子进程打分）。

子进程与 report 解析经 _run_harness / _resolve_from_report 收口，
单测 monkeypatch 之，不真实起容器（真实链路由金补丁冒烟套件覆盖）。
"""

import json

import pytest

from aite.core.types import EvalContext, TargetResponse, TestCase
from aite.metrics import swebench as swe_mod
from aite.metrics.swebench import SweResolution, _resolve_from_report


def report(resolved=(), unresolved=(), empty=(), errors=(), infra=()):
    return {
        "resolved_ids": list(resolved),
        "unresolved_ids": list(unresolved),
        "empty_patch_ids": list(empty),
        "error_ids": list(errors),
        "infra_failure_ids": list(infra),
        "schema_version": 2,
    }


class TestResolveFromReport:
    def test_resolved(self):
        value, err = _resolve_from_report(report(resolved=["i1"]), "i1")
        assert value == 1.0 and err is None

    def test_unresolved_and_empty_patch_are_zero(self):
        for rep in (report(unresolved=["i1"]), report(empty=["i1"])):
            value, err = _resolve_from_report(rep, "i1")
            assert value == 0.0 and err is None

    def test_infra_and_error_are_undecidable(self):
        """基础设施失败/错误 = 打分本身失败 → value=None（宁缺毋错），不算 0。"""
        for rep in (report(errors=["i1"]), report(infra=["i1"])):
            value, err = _resolve_from_report(rep, "i1")
            assert value is None and err

    def test_absent_instance_is_undecidable(self):
        value, err = _resolve_from_report(report(resolved=["other"]), "i1")
        assert value is None and err


def swe_case(instance_id: str) -> TestCase:
    return TestCase(id="c1", input="issue", metadata={"instance_id": instance_id})


def patch_response(patch: str | None, error: str | None = None) -> TargetResponse:
    return TargetResponse(output=patch, error=error)


def make_metric(**params) -> SweResolution:
    params.setdefault("work_dir", _tmp_work())
    return SweResolution(dataset_name="SWE-bench/SWE-bench_Lite", **params)


def _tmp_work():
    import tempfile
    return tempfile.mkdtemp(prefix="aite-swe-test-")


class TestSweResolution:
    @pytest.mark.asyncio
    async def test_empty_patch_scores_zero_without_harness(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("空补丁不应触发 harness")
        monkeypatch.setattr(swe_mod, "_run_harness", boom)
        result = await make_metric().compute(
            case=swe_case("i1"), response=patch_response("  "),
            ctx=EvalContext(run_id="r1"))
        assert result.value == 0.0
        assert result.raw["reason"] == "empty_patch"

    @pytest.mark.asyncio
    async def test_resolved_patch(self, monkeypatch, tmp_path):
        captured = {}

        def fake_harness(pred_file, **kw):
            captured["pred"] = json.loads(pred_file.read_text(encoding="utf-8"))
            captured.update(kw)
            return report(resolved=["i1"])

        monkeypatch.setattr(swe_mod, "_run_harness", fake_harness)
        metric = make_metric(work_dir=str(tmp_path), threshold=0.5)
        result = await metric.compute(
            case=swe_case("i1"), response=patch_response("diff --git a"),
            ctx=EvalContext(run_id="r1"))
        assert result.value == 1.0
        assert metric.judge_passed(result.value) is True   # passed 由 engine 按 threshold 填
        assert captured["pred"]["instance_id"] == "i1"
        assert captured["pred"]["model_patch"] == "diff --git a"
        assert captured["instance_id"] == "i1"

    @pytest.mark.asyncio
    async def test_unresolved_patch_is_zero(self, monkeypatch, tmp_path):
        monkeypatch.setattr(swe_mod, "_run_harness", lambda *a, **k: report(unresolved=["i1"]))
        result = await make_metric(work_dir=str(tmp_path)).compute(
            case=swe_case("i1"), response=patch_response("diff --git a"),
            ctx=EvalContext(run_id="r1"))
        assert result.value == 0.0

    @pytest.mark.asyncio
    async def test_harness_failure_is_undecidable(self, monkeypatch, tmp_path):
        def boom(*a, **k):
            raise RuntimeError("docker daemon not reachable")
        monkeypatch.setattr(swe_mod, "_run_harness", boom)
        result = await make_metric(work_dir=str(tmp_path)).compute(
            case=swe_case("i1"), response=patch_response("diff"),
            ctx=EvalContext(run_id="r1"))
        assert result.value is None
        assert "docker" in result.error

    @pytest.mark.asyncio
    async def test_infra_failure_is_undecidable_not_zero(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            swe_mod, "_run_harness", lambda *a, **k: report(infra=["i1"]))
        result = await make_metric(work_dir=str(tmp_path)).compute(
            case=swe_case("i1"), response=patch_response("diff"),
            ctx=EvalContext(run_id="r1"))
        assert result.value is None and result.error

    @pytest.mark.asyncio
    async def test_connector_error_propagates_as_undecidable(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("执行已失败，不应触发 harness")
        monkeypatch.setattr(swe_mod, "_run_harness", boom)
        result = await make_metric().compute(
            case=swe_case("i1"), response=patch_response(None, error="predictions file not found"),
            ctx=EvalContext(run_id="r1"))
        assert result.value is None and "not found" in result.error

    @pytest.mark.asyncio
    async def test_missing_instance_id_falls_back_to_case_id(self, monkeypatch, tmp_path):
        captured = {}

        def fake_harness(pred_file, **kw):
            captured.update(kw)
            return report(resolved=["c9"])

        monkeypatch.setattr(swe_mod, "_run_harness", fake_harness)
        case = TestCase(id="c9", input="q")   # metadata 无 instance_id
        result = await make_metric(work_dir=str(tmp_path)).compute(
            case=case, response=patch_response("diff"), ctx=EvalContext(run_id="r1"))
        assert result.value == 1.0
        assert captured["instance_id"] == "c9"

    def test_metric_contract(self):
        """aggregation=mean（多案 0/1 均值即 resolve rate）；方向：越高越好。"""
        assert SweResolution.aggregation == "mean"
        assert SweResolution.higher_is_better is True
        assert SweResolution.needs_llm is False
        make_metric()  # 构造无副作用

    def test_harness_bootstrap_compiles(self):
        """Windows 宿主 LF 引导脚本必须可编译且包含 newline 强制。"""
        compile(swe_mod._HARNESS_BOOTSTRAP, "bootstrap", "exec")
        assert "newline" in swe_mod._HARNESS_BOOTSTRAP

    def test_harness_bootstrap_wiring(self):
        """--help 走通 引导→模块导入→main() 全链（需 swebench 装在测试环境）。"""
        pytest.importorskip("swebench")
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "-c", swe_mod._HARNESS_BOOTSTRAP, "--help"],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0
        assert "run_evaluation" in proc.stdout
