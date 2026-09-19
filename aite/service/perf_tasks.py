"""性能压测域任务装配（domain-backends.md）：engine=evalscope-perf/locust，
spec 支持负载/时长/到达模型（jitter/arrival）等压测一等配置。"""

from __future__ import annotations

from aite.core.taskkit import DomainTaskKit, EventSink
from aite.service.suite_task import SuiteTaskExecutor
from aite.storage.models import PerfTaskRow
from aite.web.events import EventBus


def build_perf_task_kit(bus: EventSink | None = None) -> DomainTaskKit:
    return DomainTaskKit("perf", PerfTaskRow, SuiteTaskExecutor(),
                         bus or EventBus())
