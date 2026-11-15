"""精度评测域任务装配（domain-backends.md）：engine=evalscope 委托外部工具。

执行分发（2026-11-15 判官校准端到端）：spec.task_type=judge_calibration 的
任务走校准执行器（判官逐行打分 vs 人工标注，不走 evalscope、不建 run 行）；
其余 spec 仍是 suite raw，原样交 SuiteTaskExecutor。两形态共用 AccuracyTaskRow
与泛型域任务路由（状态机/SSE/停止桥零改动）。
"""

from __future__ import annotations

from aite.core.taskkit import DomainTaskKit, EventSink, TaskExecutor, TaskRunContext
from aite.events import EventBus
from aite.service.suite_task import SuiteTaskExecutor
from aite.storage.models import AccuracyTaskRow


class AccuracyTaskExecutor(TaskExecutor):
    """按 spec.task_type 分发：judge_calibration 走校准 runner，其余走 evalscope。"""

    async def execute(self, ctx: TaskRunContext) -> dict:
        if isinstance(ctx.spec, dict) and ctx.spec.get("task_type") == "judge_calibration":
            from aite.service.judge_calibration_task import execute_calibration

            return await execute_calibration(ctx)
        return await SuiteTaskExecutor().execute(ctx)


def build_accuracy_task_kit(bus: EventSink | None = None) -> DomainTaskKit:
    return DomainTaskKit("accuracy", AccuracyTaskRow, AccuracyTaskExecutor(),
                         bus or EventBus())
