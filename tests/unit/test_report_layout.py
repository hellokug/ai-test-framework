"""报告目录层级：``<模块>/<模型>/<数据集>/<本地时间>_<run短id>``。

suite.module（缺省 misc）前置归位——reports 按评测模块分目录的用户诉求。
"""

from datetime import datetime, timezone

from aite.core.types import RunReport
from aite.reporting.renderers import datasets_label, report_relpath


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


def test_tool_suite_datasets_from_tools_section():
    """工具域 suite 快照：datasets 在 tools 段（裸字符串），同样归位不落 unknown/。"""
    config = {"module": "accuracy", "target": {"model": "deepseek-chat"},
              "tools": {"datasets": ["aime26"]}}
    p = report_relpath(_report(config))
    assert p.parts[:3] == ("accuracy", "deepseek-chat", "aime26")
    assert datasets_label(config) == "aime26"


def test_tool_suite_local_path_dataset():
    """工具域本地数据集（rageval 场景）：路径取文件名。"""
    config = {"tools": {"datasets": ["./data/datasets/accuracy/rgb_openbook.jsonl"]}}
    assert report_relpath(_report(config)).parts[2] == "rgb_openbook.jsonl"
    assert datasets_label(config) == "rgb_openbook.jsonl"
