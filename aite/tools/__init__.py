"""工具适配层：外部评测工具的编排与结果统一入库。

suite yaml 带 ``engine: evalscope``（或其他注册工具名）时，``aite run`` 路由到
本层执行；结果写入 test_run / run_metric_summary，与自研引擎的结果在
Web 趋势中并列可比。无 engine 字段的 suite 走自研引擎，行为不变。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aite.core.types import MetricSummary, RunReport, utcnow
from aite.tools.base import (
    ToolAdapter as ToolAdapter,
)
from aite.tools.base import (
    ToolMetricRow as ToolMetricRow,
)
from aite.tools.base import (
    ToolRunRequest as ToolRunRequest,
)
from aite.tools.base import (
    ToolRunResult as ToolRunResult,
)
from aite.tools.base import (
    get_tool,
)
from aite.tools.base import (
    register_tool as register_tool,
)
from aite.tools.evalscope_adapter import EvalscopeAdapter  # noqa: F401  (registers)
from aite.utils.logging import get_logger

logger = get_logger(__name__)


def parse_tool_suite(raw: dict) -> tuple[ToolRunRequest, dict]:
    """工具型 suite yaml → (ToolRunRequest, 原始配置快照)。"""
    tools_cfg = raw.get("tools") or {}
    if not tools_cfg.get("datasets"):
        raise ValueError("tool suite requires tools.datasets (benchmark names)")
    run_name = raw.get("run_name") or "tool-run"
    stamp = utcnow().astimezone().strftime("%Y%m%d-%H%M%S")
    work_dir = (Path(raw.get("output_dir") or "./reports") / "tool"
                / str(raw["engine"]) / run_name / stamp)
    req = ToolRunRequest(
        run_name=run_name,
        target=raw.get("target") or {},
        datasets=list(tools_cfg["datasets"]),
        limit_per_subset=tools_cfg.get("limit_per_subset"),
        work_dir=work_dir,
        extra=tools_cfg.get("extra_args") or {},
        metric_thresholds=tools_cfg.get("metric_thresholds") or {},
    )
    return req, raw


async def run_tool_suite(raw: dict, repo: Any) -> RunReport:
    """执行工具型 suite：跑外部工具 → 结果统一入库 → 返回 RunReport。"""
    engine_name = raw.get("engine")
    adapter = get_tool(engine_name)
    req, cfg_snapshot = parse_tool_suite(raw)

    report = RunReport(
        run_name=req.run_name, pass_strategy="all_of", started_at=utcnow(),
        config=cfg_snapshot,
    )
    suite_id = await repo.upsert_suite(req.run_name, cfg_snapshot)
    run_id = await repo.create_run(req.run_name, cfg_snapshot, suite_id)
    report.run_id = run_id

    try:
        result = await adapter.run(req)
        summaries = [
            MetricSummary(
                metric=r.metric, dataset=r.dataset, group=r.group,
                aggregation="mean", value=r.value, threshold=r.threshold,
                passed=r.passed, count=r.count, pass_rate=None,
            )
            for r in result.rows
        ]
        # overall 行（dataset=None）：与自研引擎口径对齐；CLI 汇总表只显示 overall 行，
        # 趋势页的汇总视图也依赖它。多数据集时按 count 加权平均。
        by_metric: dict[str, list[Any]] = {}
        for r in result.rows:
            by_metric.setdefault(r.metric, []).append(r)
        for metric, mrows in by_metric.items():
            total = sum(r.count for r in mrows)
            value = (sum(r.value * r.count for r in mrows) / total) if total else None
            thr = next((r.threshold for r in mrows if r.threshold is not None), None)
            summaries.append(MetricSummary(
                metric=metric, dataset=None, group=None, aggregation="mean",
                value=value, threshold=thr, count=total, pass_rate=None,
                passed=(value >= thr) if (thr is not None and value is not None) else None,
            ))
        report.summaries = summaries
        n_samples = sum(int(v.get("summary", {}).get("n_samples", 0))
                        for v in result.perf.values())
        report.total_cases = n_samples or (result.rows[0].count if result.rows else 0)
        failed = any(r.passed is False for r in result.rows)
        report.status = "failed" if failed else "passed"
        report.details["tool"] = engine_name
        report.details["reports"] = result.report_files
        report.details["raw_score"] = result.raw_score
        if result.perf:
            report.details["perf"] = result.perf
        report.finished_at = utcnow()

        for s in summaries:
            await repo.save_metric_summary(run_id, s)
        await repo.update_run(
            run_id, status=report.status,
            overall_score=next(iter(result.raw_score.values()), None),
            total_cases=report.total_cases,
            finished_at=report.finished_at,
        )
    except Exception as exc:  # noqa: BLE001 - run-level failure must be persisted
        logger.exception("tool run failed")
        report.status = "error"
        report.details["error"] = f"{type(exc).__name__}: {exc}"
        await repo.update_run(run_id, status="error", error=str(exc)[:2000],
                              finished_at=utcnow())
    return report
