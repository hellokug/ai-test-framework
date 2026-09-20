"""suite 型域任务执行器：functional / accuracy / perf 三域共用（domain-backends.md §6）。

spec 即 suite raw（与 CLI `aite run`、POST /runs 的 YAML/dict 同构），统一经
execute_suite 引擎管道执行（tool 域 subprocess 三连 / 自研域 engine），结果落
公共 run 存储，任务表 result 只存概要引用。停止语义：执行协程随 cancel 终止；
子进程型引擎的进程组强杀由各域 executor 扩展 cancel（见设计稿 §9 待办）。
"""

from __future__ import annotations

from aite.core.taskkit import TaskExecutor, TaskRunContext
from aite.service.run_service import execute_suite


def suite_summary(report) -> dict:
    """RunReport → 套件型任务统一概要（suite 型域任务 result 形态，§10）。"""
    return {
        "run_id": report.run_id,
        "run_name": report.run_name,
        "status": report.status,
        "total_cases": report.total_cases,
        "error_cases": report.error_cases,
        "reports": report.details.get("reports", {}),
    }


class SuiteTaskExecutor(TaskExecutor):
    """spec = suite raw；result 见 suite_summary（域差异化结果视图在 C1-C4
    展示端点层做）。"""

    async def execute(self, ctx: TaskRunContext) -> dict:
        report = await execute_suite(ctx.spec, ctx.repo,
                                     event_cb=ctx.emit_raw)
        return suite_summary(report)

    async def cancel(self, ctx: TaskRunContext) -> None:
        # 自研引擎在 await 点协作退出；工具域子进程的进程组终止待各域补强
        ctx.cancel_event.set()
