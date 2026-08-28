"""Builtin renderers. Each writes into ``dest`` directory and returns the path.

- json : machine-readable full report
- html : standalone HTML (embedded CSS, no external assets)
- rich : human console table (also saved as text)
- junit: JUnit XML for CI systems
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from aite.core.interfaces import ReportRenderer
from aite.core.registry import register_renderer

if TYPE_CHECKING:
    from aite.core.types import RunReport


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
        template = self._env.get_template("report.html.j2")
        html = template.render(
            report=report, overall=overall, grouped=grouped,
            failed_cases=failed[:500], failed_count=len(failed),
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
