"""suite 型域任务执行器：functional / accuracy / perf 三域共用（domain-backends.md §6）。

spec 即 suite raw（与 CLI `aite run`、POST /runs 的 YAML/dict 同构），统一经
execute_suite 引擎管道执行（tool 域 subprocess 三连 / 自研域 engine），结果落
公共 run 存储，任务表 result 只存概要引用。停止语义：执行协程随 cancel 终止；
子进程型引擎的进程组强杀由各域 executor 扩展 cancel（见设计稿 §9 待办）。
"""

from __future__ import annotations

from aite.core.taskkit import TaskExecutor, TaskRunContext
from aite.service.run_service import execute_suite
from aite.service.version_results import archive_maybe


def suite_summary(report) -> dict:
    """RunReport → 套件型任务统一概要（suite 型域任务 result 形态，§10）。"""
    summary = {
        "run_id": report.run_id,
        "run_name": report.run_name,
        "status": report.status,
        "total_cases": report.total_cases,
        "error_cases": report.error_cases,
        "reports": report.details.get("reports", {}),
    }
    summary.update(evaluation_stats(report))
    return summary


def evaluation_stats(report) -> dict:
    """评估层用例统计（functional pytest 行专有；其他域无 pytest: 行 → 空缺省）。

    与 version_results.extract_dimensions 同源口径：dataset=pytest:<场景> 行的
    value=场景通过率、count=用例数，按 count 加权。执行层 status（阈值未配置时
    进程跑完即 passed，≠用例全过）之外的评估层真值，任务列表直读免二次查询。
    """
    rows = [s for s in getattr(report, "summaries", None) or []
            if (getattr(s, "dataset", None) or "").startswith("pytest:")
            and s.value is not None]
    if not rows:
        return {}
    total = sum(s.count or 0 for s in rows)
    rate = (sum((s.value or 0) * (s.count or 0) for s in rows) / total) if total else None
    passed = sum(round((s.value or 0) * (s.count or 0)) for s in rows)
    return {"pass_rate": round(rate, 6) if rate is not None else None,
            "evaluated_cases": total, "passed_cases": passed}


class SuiteTaskExecutor(TaskExecutor):
    """spec = suite raw；result 见 suite_summary（域差异化结果视图在 C1-C4
    展示端点层做）。functional 域任务终态后顺手归档版本结果（spec.version
    标注才归档；失败仅告警，见 version_results.archive_maybe）。"""

    async def execute(self, ctx: TaskRunContext) -> dict:
        # resume=False：任务域重跑必须真执行（断点续推复用会让重跑瞬完成不跑样本，
        # owner 2026-11-12 反馈「重跑没跑成功」）；续推语义保留给 CLI `aite run --resume`
        report = await execute_suite(ctx.spec, ctx.repo,
                                     event_cb=ctx.emit_raw, resume=False,
                                     run_cb=ctx.run_cb)
        await archive_maybe(ctx.repo, ctx.spec, report)
        return suite_summary(report)

    async def cancel(self, ctx: TaskRunContext) -> None:
        # 自研引擎在 await 点协作退出；工具域子进程的进程组终止待各域补强
        ctx.cancel_event.set()
