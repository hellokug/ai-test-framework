"""render_report_formats 共用渲染入口：内建格式逐个落盘，失败格式错误串占位不抛。

回归：曾把渲染实例当函数调（``resolve_renderer(fmt)()(report, ...)`` 漏
``.render``），三个内建格式全灭且只留 warning——此处断言每个格式都真实落盘。
"""

from datetime import datetime, timezone
from pathlib import Path

from aite.core.types import RunReport
from aite.reporting.renderers import render_report_formats


def _report() -> RunReport:
    return RunReport(
        run_name="r", status="passed", pass_strategy="all_of",
        started_at=datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc),
        config={"module": "accuracy", "target": {"model": "m"}, "datasets": []},
    )


def test_all_builtin_formats_render(tmp_path):
    written = render_report_formats(_report(), tmp_path, ["rich", "json", "html", "junit"])
    assert set(written) == {"rich", "json", "html", "junit"}
    for fmt, path in written.items():
        assert not path.startswith("error:"), f"{fmt}: {path}"
        assert Path(path).exists()


def test_bad_format_isolated_not_fatal(tmp_path):
    """单个格式失败不中断其他格式（原则 9：错误串占位显式可见）。"""
    written = render_report_formats(_report(), tmp_path, ["nope", "json"])
    assert written["nope"].startswith("error:")
    assert not written["json"].startswith("error:")
    assert Path(written["json"]).exists()
