"""报告目录层级：``<模块>/<模型>/<数据集>/<本地时间>_<run短id>``。

suite.module（缺省 misc）前置归位——reports 按评测模块分目录的用户诉求。
"""

from datetime import datetime, timezone

from aite.core.types import RunReport
from aite.reporting.renderers import report_relpath


def _report(config: dict) -> RunReport:
    return RunReport(
        run_name="r", pass_strategy="all_of",
        started_at=datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc),
        config=config,
    )


def test_module_prepended():
    p = report_relpath(_report({
        "module": "accuracy", "target": {"model": "qwen"},
        "datasets": [{"type": "builtin", "id": "gsm8k"}]}))
    assert p.parts[:3] == ("accuracy", "qwen", "gsm8k")


def test_default_module_is_misc():
    """未声明 module 的旧 suite 落 misc/，向后兼容。"""
    p = report_relpath(_report({"target": {"model": "m"}, "datasets": []}))
    assert p.parts[0] == "misc"
