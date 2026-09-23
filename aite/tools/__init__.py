"""工具适配层：外部评测工具的编排与结果统一入库。

suite yaml 带 ``engine: evalscope``（或其他注册工具名）时，``aite run`` 路由到
本层执行；结果写入 test_run / run_metric_summary，与自研引擎的结果在
Web 趋势中并列可比。无 engine 字段的 suite 走自研引擎，行为不变。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aite import config
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


def build_overall_rows(rows: list[ToolMetricRow]) -> list[MetricSummary]:
    """工具域 overall 行（dataset=None）构建：与自研引擎口径对齐。

    CLI 汇总表只显示 overall 行，趋势页汇总视图也依赖它。多档/多数据集按
    count 加权平均；阈值方向取该 metric 组首行（同 metric 方向应一致，
    如 perf 的延迟类 <=）。
    """
    by_metric: dict[str, list[ToolMetricRow]] = {}
    for r in rows:
        by_metric.setdefault(r.metric, []).append(r)
    overall: list[MetricSummary] = []
    for metric, mrows in by_metric.items():
        total = sum(r.count for r in mrows)
        value = (sum(r.value * r.count for r in mrows) / total) if total else None
        thr = next((r.threshold for r in mrows if r.threshold is not None), None)
        higher = mrows[0].higher_is_better
        overall.append(MetricSummary(
            metric=metric, dataset=None, group=None, aggregation="mean",
            value=value, threshold=thr, count=total, pass_rate=None,
            higher_is_better=higher,
            passed=((value >= thr) if higher else (value <= thr))
            if (thr is not None and value is not None) else None,
        ))
    return overall


def serialize_summaries(rows: list[ToolMetricRow]) -> list[dict]:
    """指标行 → timeseries.json 的 ``summaries`` 形状（大屏统计表数据源）。

    overall 行（dataset=None，与 CLI 汇总表同源）在前、档级行在后；逐字段
    平铺为可 JSON 序列化的 dict，value/threshold/passed 允许 None（未配阈值
    → passed=None → 前端显示 "-"）。
    """

    def _dict(metric, dataset, value, count, threshold, passed, higher):
        return {"metric": metric, "dataset": dataset, "value": value,
                "count": count, "threshold": threshold, "passed": passed,
                "higher_is_better": higher}

    out = [_dict(r.metric, None, r.value, r.count, r.threshold, r.passed,
                 r.higher_is_better) for r in build_overall_rows(rows)]
    out += [_dict(r.metric, r.dataset, r.value, r.count, r.threshold,
                  r.passed, r.higher_is_better) for r in rows]
    return out


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
    work_dir = (Path(raw.get("output_dir") or config.DEFAULT_REPORTS_DIR) / "tool"
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


async def execute_tool(raw: dict, work_dir: Path | None = None
                       ) -> tuple[ToolRunResult, dict, ToolRunRequest]:
    """纯执行：构造请求 → 适配器执行 → 结果（不落库，agent 回传场景用）。

    work_dir 缺省走 parse_tool_suite 的 reports/tool/<engine>/<run>/<stamp>。
    """
    engine_name = raw.get("engine")
    adapter = get_tool(engine_name)
    req, cfg_snapshot = parse_tool_suite(raw)
    if work_dir is not None:
        req.work_dir = Path(work_dir)
    result = await adapter.run(req)
    return result, cfg_snapshot, req


def build_tool_report(raw: dict, repo: Any, result: ToolRunResult,
                      work_dir: Path) -> RunReport:
    """纯入库（同步部分）：ToolRunResult → RunReport 聚合与 details（不写库）。"""
    engine_name = raw.get("engine")
    req, cfg_snapshot = parse_tool_suite(raw)
    report = RunReport(
        run_name=req.run_name, pass_strategy="all_of", started_at=utcnow(),
        config=cfg_snapshot,
    )
    summaries = [
        MetricSummary(
            metric=r.metric, dataset=r.dataset, group=r.group,
            aggregation="mean", value=r.value, threshold=r.threshold,
            passed=r.passed, count=r.count, pass_rate=None,
            higher_is_better=r.higher_is_better,
        )
        for r in result.rows
    ]
    summaries.extend(build_overall_rows(result.rows))
    report.summaries = summaries
    n_samples = sum(int(v.get("summary", {}).get("n_samples", 0))
                    for v in result.perf.values())
    # total_cases 回退：首个 metric 组的 count 总和（多数据集/多场景求和，而非首行）
    first_metric = result.rows[0].metric if result.rows else None
    total_counts = sum(r.count for r in result.rows if r.metric == first_metric)
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
    report.details["work_dir"] = str(work_dir)
    report.finished_at = utcnow()
    _ = repo   # 签名对齐 persist_tool_result；此处不触库
    return report


async def persist_tool_result(raw: dict, repo: Any, result: ToolRunResult,
                              work_dir: Path, run_id: str | None = None,
                              extra_details: dict | None = None) -> RunReport:
    """纯入库（异步）：指标汇总落库 → 终态与 details 回写。

    master 回传场景与本地执行共用（agent 场景 work_dir 为 master 侧重建目录）；
    run_id 传入时复用已建 run 行（本地执行先建行保持 running 中间态可见）。
    """
    req, cfg_snapshot = parse_tool_suite(raw)
    report = RunReport(
        run_name=req.run_name, pass_strategy="all_of", started_at=utcnow(),
        config=cfg_snapshot,
    )
    if run_id is None:
        suite_id = await repo.upsert_suite(req.run_name, cfg_snapshot)
        run_id = await repo.create_run(req.run_name, cfg_snapshot, suite_id)
    report.run_id = run_id

    summaries = [
        MetricSummary(
            metric=r.metric, dataset=r.dataset, group=r.group,
            aggregation="mean", value=r.value, threshold=r.threshold,
            passed=r.passed, count=r.count, pass_rate=None,
            higher_is_better=r.higher_is_better,
        )
        for r in result.rows
    ]
    summaries.extend(build_overall_rows(result.rows))
    report.summaries = summaries
    n_samples = sum(int(v.get("summary", {}).get("n_samples", 0))
                    for v in result.perf.values())
    first_metric = result.rows[0].metric if result.rows else None
    total_counts = sum(r.count for r in result.rows if r.metric == first_metric)
    report.total_cases = n_samples or total_counts
    failed = any(r.passed is False for r in result.rows)
    report.status = "failed" if failed else "passed"
    report.details["tool"] = raw.get("engine")
    report.details["reports"] = result.report_files
    report.details["raw_score"] = result.raw_score
    if result.failures:
        report.details["failures"] = result.failures[:50]
    if result.perf:
        report.details["perf"] = result.perf
    if extra_details:
        report.details.update(extra_details)
    report.finished_at = utcnow()

    for s in summaries:
        await repo.save_metric_summary(run_id, s)
    await repo.update_run(
        run_id, status=report.status,
        overall_score=next(iter(result.raw_score.values()), None),
        total_cases=report.total_cases,
        finished_at=report.finished_at,
        details={"reports": result.report_files, "work_dir": str(work_dir),
                 "failures": result.failures[:50]},
    )
    return report


async def run_tool_suite(raw: dict, repo: Any,
                         event_cb: Callable[[dict], None] | None = None
                         ) -> RunReport:
    """本地执行工具型 suite：先建 run 行（running 可见）→ 执行 → 入库。

    event_cb：运行期进度事件出口（Web SSE / CLI 打印共用）；适配器经
    req.progress_cb 回传（如 evalscope 日志解析出的 done/total），total
    已知即回写 run 行 total_cases。"""
    engine_name = raw.get("engine")
    req, cfg_snapshot = parse_tool_suite(raw)
    report = RunReport(
        run_name=req.run_name, pass_strategy="all_of", started_at=utcnow(),
        config=cfg_snapshot,
    )
    suite_id = await repo.upsert_suite(req.run_name, cfg_snapshot)
    run_id = await repo.create_run(req.run_name, cfg_snapshot, suite_id)
    report.run_id = run_id
    last_total: int | None = None
    bg_updates: set[asyncio.Task] = set()

    def progress_cb(ev: dict) -> None:
        """适配器进度事件 → event_cb（SSE/CLI）+ total 已知即异步回写 run 行。"""
        nonlocal last_total
        if event_cb:
            event_cb(ev)
        total = ev.get("total")
        if total and total != last_total:
            last_total = total

            async def _write_total():
                with contextlib.suppress(Exception):
                    await repo.update_run(run_id, total_cases=int(total))

            task = asyncio.create_task(_write_total())
            bg_updates.add(task)
            task.add_done_callback(bg_updates.discard)

    req.progress_cb = progress_cb
    try:
        adapter = get_tool(engine_name)
        result = await adapter.run(req)
        if bg_updates:
            await asyncio.gather(*bg_updates, return_exceptions=True)
        return await persist_tool_result(raw, repo, result, req.work_dir, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 - run-level failure must be persisted
        logger.exception("tool run failed")
        report.status = "error"
        report.details["error"] = f"{type(exc).__name__}: {exc}"
        await repo.update_run(run_id, status="error", error=str(exc)[:2000],
                              finished_at=utcnow(),
                              details={"work_dir": str(req.work_dir)})
        return report
