

class TestEvaluationStats:
    """evaluation_stats：评估层用例统计（functional pytest 行 → 整体通过率）。"""

    def _report(self, summaries):
        from types import SimpleNamespace
        return SimpleNamespace(
            run_id="r1", run_name="n", status="passed", total_cases=2,
            error_cases=0, details={}, summaries=summaries,
        )

    def test_weighted_pass_rate(self):
        from types import SimpleNamespace

        from aite.service.suite_task import suite_summary
        rows = [
            SimpleNamespace(dataset="pytest:s1", value=1.0, count=3),
            SimpleNamespace(dataset="pytest:s2", value=0.0, count=1),
        ]
        summary = suite_summary(self._report(rows))
        assert summary["pass_rate"] == 0.75           # (1.0*3 + 0.0*1) / 4
        assert summary["evaluated_cases"] == 4
        assert summary["passed_cases"] == 3           # 1.0*3 + 0.0*1
        assert summary["status"] == "passed"          # 执行层与评估层并存

    def test_non_functional_rows_absent(self):
        from types import SimpleNamespace

        from aite.service.suite_task import suite_summary
        rows = [SimpleNamespace(dataset="gsm8k", value=0.9, count=5)]
        summary = suite_summary(self._report(rows))
        assert "pass_rate" not in summary             # 非 pytest 行不产评估统计

    def test_none_values_skipped(self):
        from types import SimpleNamespace

        from aite.service.suite_task import suite_summary
        rows = [SimpleNamespace(dataset="pytest:s1", value=None, count=3)]
        summary = suite_summary(self._report(rows))
        assert "pass_rate" not in summary


class TestSuiteRunError:
    """run 级失败（report.status=error：框架/评测基建挂了）必须抛 SuiteRunError →
    任务层收口 failed 且 error 文案可见；评估结论 failed/passed（run 跑成了）
    照常返回 summary。背景（owner 2026-11-15）：evalscope swebench 起不来时
    run error 曾被吞成任务「已成功」，详情页无任何报错。"""

    def _report(self, status, details=None):
        from types import SimpleNamespace
        return SimpleNamespace(
            run_id="r1", run_name="n", status=status, total_cases=2,
            error_cases=0, details=details if details is not None else {},
            summaries=[],
        )

    async def _execute(self, report, monkeypatch):
        from aite.service import suite_task

        async def fake_execute_suite(spec, repo, **kwargs):
            return report

        monkeypatch.setattr(suite_task, "execute_suite", fake_execute_suite)
        return await suite_task.SuiteTaskExecutor().execute(self._ctx())

    def _ctx(self):
        from aite.core.taskkit import DomainTaskKit, TaskRunContext
        from aite.storage.models import PerfTaskRow
        from aite.web.events import EventBus

        kit = DomainTaskKit("perf", PerfTaskRow, None, EventBus())
        return TaskRunContext(kit, "t1", {"engine": "evalscope"}, repo=None)

    async def test_error_report_raises_with_detail(self, monkeypatch):
        import pytest

        from aite.service.suite_task import SuiteRunError
        report = self._report("error", {"error": "evalscope task failed: boom"})
        with pytest.raises(SuiteRunError) as ei:
            await self._execute(report, monkeypatch)
        assert "evalscope task failed: boom" in str(ei.value)

    async def test_error_without_detail_still_raises(self, monkeypatch):
        import pytest

        from aite.service.suite_task import SuiteRunError
        with pytest.raises(SuiteRunError) as ei:
            await self._execute(self._report("error"), monkeypatch)
        assert "run_id=r1" in str(ei.value)       # 兜底文案可追溯到 run

    async def test_passed_report_returns_summary(self, monkeypatch):
        report = self._report("passed")
        result = await self._execute(report, monkeypatch)
        assert result["status"] == "passed" and result["run_id"] == "r1"

    async def test_evaluation_failed_still_succeeds_task(self, monkeypatch):
        """评估结论 failed（指标低分）≠ 任务失败：run 跑成了，任务 succeeded。"""
        result = await self._execute(self._report("failed"), monkeypatch)
        assert result["status"] == "failed"        # 契约：不抛错
