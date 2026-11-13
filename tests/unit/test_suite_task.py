

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
