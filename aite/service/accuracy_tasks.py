"""精度评测域任务装配（domain-backends.md）：engine=evalscope 委托外部工具。"""

from __future__ import annotations

from aite.core.taskkit import DomainTaskKit, EventSink
from aite.events import EventBus
from aite.service.suite_task import SuiteTaskExecutor
from aite.storage.models import AccuracyTaskRow


def build_accuracy_task_kit(bus: EventSink | None = None) -> DomainTaskKit:
    return DomainTaskKit("accuracy", AccuracyTaskRow, SuiteTaskExecutor(),
                         bus or EventBus())
