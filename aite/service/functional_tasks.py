"""功能测试域任务装配（domain-backends.md）：CLI/Web 共用，executor 复用统一管道。"""

from __future__ import annotations

from aite.core.taskkit import DomainTaskKit, EventSink
from aite.service.suite_task import SuiteTaskExecutor
from aite.storage.models import FunctionalTaskRow
from aite.web.events import EventBus


def build_functional_task_kit(bus: EventSink | None = None) -> DomainTaskKit:
    """bus 缺省新建（CLI 同步形态）；Web 侧传 state.bus 共享 SSE 通道。"""
    return DomainTaskKit("functional", FunctionalTaskRow, SuiteTaskExecutor(),
                         bus or EventBus())
