"""性能压测域任务装配（domain-backends.md）：engine=evalscope-perf/locust，
spec 支持负载/时长/到达模型（jitter/arrival）等压测一等配置。"""

from __future__ import annotations

from aite.core.taskkit import DomainTaskKit, EventSink
from aite.events import EventBus
from aite.service.suite_task import SuiteTaskExecutor
from aite.storage.models import PerfTaskRow

# 停止桥等待执行器自行收口的上限（秒）：立即停止要「排空在途请求」——单条
# LLM 长生成可达数分钟，60s 缺省会在排空中途强杀；兜底杀树由适配器监督循环
# 的排空预算（GRACEFUL_DRAIN_BUDGET_S）先行承担，这里是最终兜底。
PERF_STOP_TIMEOUT_S = 3600.0


def build_perf_task_kit(bus: EventSink | None = None) -> DomainTaskKit:
    kit = DomainTaskKit("perf", PerfTaskRow, SuiteTaskExecutor(), bus or EventBus())
    kit.stop_timeout = PERF_STOP_TIMEOUT_S
    return kit
