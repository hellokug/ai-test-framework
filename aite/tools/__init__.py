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
    list_tools as list_tools,
)
from aite.tools.base import (
    register_tool as register_tool,
)
from aite.tools.evalscope_adapter import EvalscopeAdapter  # noqa: F401  (registers)
from aite.tools.locust_adapter import LocustAdapter  # noqa: F401  (registers)
from aite.tools.perf_adapter import PerfAdapter  # noqa: F401  (registers)
from aite.tools.pytest_adapter import PytestAdapter  # noqa: F401  (registers)
from aite.utils.logging import get_logger

logger = get_logger(__name__)


def parse_tool_suite(raw: dict) -> tuple[ToolRunRequest, dict]:
    """工具型 suite yaml → (ToolRunRequest, 原始配置快照)。

    pytest 引擎（tools.tests_dir 模式）下 datasets 可空 = 跑该目录全部场景；
    evalscope-perf 引擎带 perf 段即可（datasets/tests_dir 均不要求）。
    """
    tools_cfg = raw.get("tools") or {}
    tests_dir = tools_cfg.get("tests_dir")
    perf_cfg = raw.get("perf")
    if perf_cfg is not None and not isinstance(perf_cfg, dict):
        raise ValueError("perf 段须为对象（prompt/parallel/number/...）")
    if not tools_cfg.get("datasets") and not tests_dir and not perf_cfg:
        raise ValueError("tool suite requires tools.datasets (benchmark names)")
    run_name = raw.get("run_name") or "tool-run"
    stamp = utcnow().astimezone().strftime("%Y%m%d-%H%M%S")
    work_dir = (Path(raw.get("output_dir") or "./reports") / "tool"
                / str(raw["engine"]) / run_name / stamp)
    mt = tools_cfg.get("metric_thresholds")
    cases = tools_cfg.get("cases")
    if cases is not None and (not isinstance(cases, list)
                              or not all(isinstance(c, str) and c for c in cases)):
        raise ValueError("tools.cases 须为用例 id 字符串数组（省略 = 全部用例）")
    req = ToolRunRequest(
        run_name=run_name,
        target=raw.get("target") or {},
        datasets=list(tools_cfg.get("datasets") or []),
        limit_per_subset=tools_cfg.get("limit_per_subset"),
        work_dir=work_dir,
        extra=tools_cfg.get("extra_args") or {},
        metric_thresholds=mt if isinstance(mt, dict) else {},
        auto_thresholds=(mt == "auto"),
        tests_dir=tests_dir,
        cases=cases,
        perf=perf_cfg or {},
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
                higher_is_better=r.higher_is_better,
            )
            for r in result.rows
        ]
        # overall 行（dataset=None）：与自研引擎口径对齐；CLI 汇总表只显示 overall 行，
        # 趋势页的汇总视图也依赖它。多数据集时按 count 加权平均；阈值方向取
        # 该 metric 组首行（同 metric 方向应一致，如 perf 的延迟类 <=）。
        by_metric: dict[str, list[Any]] = {}
        for r in result.rows:
            by_metric.setdefault(r.metric, []).append(r)
        for metric, mrows in by_metric.items():
            total = sum(r.count for r in mrows)
            value = (sum(r.value * r.count for r in mrows) / total) if total else None
            thr = next((r.threshold for r in mrows if r.threshold is not None), None)
            higher = mrows[0].higher_is_better
            summaries.append(MetricSummary(
                metric=metric, dataset=None, group=None, aggregation="mean",
                value=value, threshold=thr, count=total, pass_rate=None,
                higher_is_better=higher,
                passed=((value >= thr) if higher else (value <= thr))
                if (thr is not None and value is not None) else None,
            ))
        report.summaries = summaries
        n_samples = sum(int(v.get("summary", {}).get("n_samples", 0))
                        for v in result.perf.values())
        # total_cases 回退：首个 metric 组的 count 总和（多数据集/多场景求和，而非首行）
        first_group = next(iter(by_metric.values()), [])
        total_counts = sum(r.count for r in first_group)
        report.total_cases = n_samples or total_counts
        failed = any(r.passed is False for r in result.rows)
        report.status = "failed" if failed else "passed"
        report.details["tool"] = engine_name
        report.details["reports"] = result.report_files
        report.details["raw_score"] = result.raw_score
        if result.failures:   # 用例级失败详情（CLI 打印，入库不含 details 无膨胀）
            report.details["failures"] = result.failures[:50]
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
