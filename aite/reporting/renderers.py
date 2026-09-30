"""Builtin renderers. Each writes into ``dest`` directory and returns the path.

- json : machine-readable full report
- html : standalone HTML (embedded CSS, no external assets)
- rich : human console table (also saved as text)
- junit: JUnit XML for CI systems
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from aite.core.aggregator import execution_status
from aite.core.interfaces import ReportRenderer
from aite.core.registry import register_renderer
from aite.core.types import utcnow


def render_report_formats(report, out_dir: Path,
                          formats: list[str]) -> dict[str, str]:
    """按格式列表渲染报告 → {格式: 文件路径}（engine 与工具域共用的单一实现）。

    单个格式渲染失败不中断其他格式（错误串占位，原则 9 显式可见）。
    """
    from aite.core.registry import resolve_renderer
    from aite.utils.logging import get_logger

    logger = get_logger("renderers")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for fmt in formats or []:
        try:
            path = resolve_renderer(fmt)().render(report, out_dir)
            written[fmt] = str(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("renderer %s failed: %s", fmt, exc)
            written[fmt] = f"error: {exc}"
    return written


def _failing_reason(r, threshold: float | None, higher: bool) -> str:
    """bad-case 的「为什么不通过」：判定错误 / 未配阈值 / 值与阈值的方向关系。"""
    if r.error:
        return str(r.error).splitlines()[0]
    if threshold is None:
        return "未配置阈值（仅记录，不参与判定）"
    direction = "<" if higher else ">"
    return f"value {r.value:.4f} {direction} 阈值 {threshold:g}，未达标"

if TYPE_CHECKING:
    from aite.core.types import RunReport


def _path_safe(s: str) -> str:
    """净化 Windows 路径非法字符（如 ``qwen2.5:3b`` 的冒号 → ``qwen2.5-3b``）。"""
    return re.sub(r'[\\/:*?"<>|]+', "-", s).strip(". ") or "unknown"


def _config_datasets(config: dict) -> list:
    """suite 数据集清单：自研引擎在 ``datasets``，工具域 suite 快照在
    ``tools.datasets``（裸字符串基准名/本地路径）。"""
    ds = config.get("datasets")
    if not ds:
        ds = (config.get("tools") or {}).get("datasets")
    return ds if isinstance(ds, list) else []


def _dataset_ident(d) -> str:
    """数据集标识：dict 取 id，local 取路径文件名；工具域快照为裸字符串。"""
    if isinstance(d, str):
        return d.replace("\\", "/").split("/")[-1]
    return str(d.get("id") or str(d.get("path") or "").replace("\\", "/").split("/")[-1])


def report_relpath(report: RunReport) -> Path:
    """报告目录层级：``<模块>/<模型>/<数据集>/<本地时间>_<run短id>``。

    第一层模块（suite.module，缺省 ``misc`` 向后兼容）、第二层模型、
    第三层数据集（多个用 ``+`` 连接）、第四层时间；
    名称均做 Windows 路径净化；短 id 保证同秒不冲突。
    """
    started = (report.started_at or utcnow()).astimezone()  # 目录名用本地时间更直觉
    module = _path_safe(str(report.config.get("module") or "misc"))
    target = report.config.get("target") or {}
    model = _path_safe(str(target.get("model") or "unknown"))
    idents = [_path_safe(_dataset_ident(d)) for d in _config_datasets(report.config)]
    ds_dir = "+".join(idents) if idents else "unknown"
    if len(ds_dir) > 50:  # 防路径过长
        ds_dir = f"{idents[0]}+{len(idents) - 1}more"
    leaf = f"{started.strftime('%Y%m%d-%H%M%S')}_{(report.run_id or 'latest')[:8]}"
    return Path(module) / Path(model) / ds_dir / leaf


def datasets_label(config: dict) -> str:
    """紧凑数据集描述：``builtin/gsm8k, local/business_qa.jsonl, aime26``。"""
    parts = [
        _dataset_ident(d) if isinstance(d, str) else
        "/".join(x for x in (str(d.get("type", "?")), _dataset_ident(d)) if x)
        for d in _config_datasets(config)
    ]
    return ", ".join(parts)


@register_renderer("json")
class JsonRenderer(ReportRenderer):
    name = "json"

    def render(self, report: RunReport, dest: Path) -> Path:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "report.json"
        path.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


@register_renderer("html")
class HtmlRenderer(ReportRenderer):
    name = "html"

    _env = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
        autoescape=select_autoescape(["html", "j2"]),
    )

    def render(self, report: RunReport, dest: Path) -> Path:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "report.html"
        overall = [s for s in report.summaries if s.dataset is None and s.group is None]
        grouped = [s for s in report.summaries if s.dataset is not None or s.group is not None]
        failed = [r for r in report.case_metrics
                  if r.passed is False or (r.error and r.value is None)]
        payloads = (report.details or {}).get("case_payloads") or {}
        thresholds = {s.metric: s.threshold for s in report.summaries}
        higher = {s.metric: s.higher_is_better for s in report.summaries}
        failed_rows = [{
            "r": r,
            "payload": payloads.get(r.case_id),
            "why": _failing_reason(r, thresholds.get(r.metric), higher.get(r.metric, True)),
        } for r in failed[:500]]
        template = self._env.get_template("report.html.j2")
        html = template.render(
            report=report, overall=overall, grouped=grouped,
            failed_cases=failed_rows, failed_count=len(failed),
            datasets=datasets_label(report.config),
            exec_status=execution_status(report),
        )
        path.write_text(html, encoding="utf-8")
        return path


@register_renderer("rich")
class RichRenderer(ReportRenderer):
    name = "rich"

    def render(self, report: RunReport, dest: Path) -> Path:
        from rich.console import Console
        from rich.table import Table

        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        table = Table(title=f"aite report - {report.run_name} [{report.status}]")
        for col, justify in (("metric", "left"), ("value", "right"), ("threshold", "right"),
                             ("agg", "left"), ("n", "right"), ("pass", "center"),
                             ("dataset/group", "left")):
            table.add_column(col, justify=justify)  # type: ignore[arg-type]
        for s in report.summaries:
            table.add_row(
                s.metric,
                "—" if s.value is None else f"{s.value:.4f}",
                "—" if s.threshold is None else f"{s.threshold:g}",
                s.aggregation, str(s.count),
                {True: "[green]PASS[/green]", False: "[red]FAIL[/red]",
                 None: "-"}[s.passed],
                "/".join(x for x in (s.dataset, s.group) if x) or "overall",
            )
        console = Console(record=True)
        target = report.config.get("target") or {}
        if target:
            console.print(
                f"target: {target.get('type')} | model: {target.get('model')}"
                + (f" | {target['base_url']}" if target.get("base_url") else "")
            )
        label = datasets_label(report.config)
        if label:
            console.print(f"datasets: {label}")
        console.print(table)
        path = dest / "report.txt"
        path.write_text(console.export_text(), encoding="utf-8")
        # also echo to stdout for CLI use
        Console().print(table)
        return path


@register_renderer("junit")
class JunitRenderer(ReportRenderer):
    name = "junit"

    def render(self, report: RunReport, dest: Path) -> Path:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "junit.xml"
        failures = sum(1 for s in report.summaries if s.passed is False)
        testsuites = ET.Element("testsuites", {
            "name": report.run_name, "tests": str(len(report.summaries)),
            "failures": str(failures),
            "time": _seconds(report),
        })
        suite = ET.SubElement(testsuites, "testsuite", {
            "name": report.run_name, "tests": str(len(report.summaries)),
            "failures": str(failures),
        })
        for s in report.summaries:
            case = ET.SubElement(suite, "testcase", {
                "classname": "/".join(x for x in (s.dataset, s.group) if x) or "overall",
                "name": s.metric,
            })
            if s.passed is False:
                ET.SubElement(case, "failure", {
                    "message": (f"{s.metric}={s.value} "
                                f"{'<' if s.higher_is_better else '>'} threshold {s.threshold}"),
                    "type": "ThresholdBreach",
                }).text = (
                    f"aggregation={s.aggregation} count={s.count} "
                    f"value={s.value} threshold={s.threshold} pass_rate={s.pass_rate}")
        ET.indent(testsuites, space="  ")
        ET.ElementTree(testsuites).write(path, encoding="utf-8", xml_declaration=True)
        return path


def _seconds(report: RunReport) -> str:
    if report.started_at and report.finished_at:
        return f"{(report.finished_at - report.started_at).total_seconds():.2f}"
    return "0"
