"""公共任务框架 TaskKit：四域独立任务管理的单一实现（docs/domain-backends.md）。

一份代码、四份数据——每域一张任务表 + 一个 TaskExecutor 实现，DomainTaskKit
实例化驱动统一状态机：pending → running → stopping → cancelled，或
running → succeeded / failed；running/stopping 残留由 startup mark_stale 收敛。
状态常量单一来源 core.types（原则 8）；DB 访问全部经 repository 泛型方法（原则 6）；
事件通道走 EventSink 协议（web.events.EventBus 满足），core 不依赖 web（原则 2）。

状态转移一律走 repo 的 expect_status 条件更新：终态写入方以「先到者得」裁决
（原则 8 唯一出口归属），后到者重读 DB 以 DB 为准，不覆盖。
"""

from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from typing import Any, Protocol

from aite.core.types import (
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_PENDING,
    TASK_RUNNING,
    TASK_STOPPING,
    TASK_SUCCEEDED,
    TASK_TERMINAL,
    utcnow,
)
from aite.utils.logging import get_logger

logger = get_logger("taskkit")

STOP_TIMEOUT_S = 60.0              # stop 协作等待上限，超时强转 cancelled


class TaskNotFoundError(LookupError):
    """任务不存在（HTTP 404 对应）。"""


class TaskStateError(Exception):
    """状态机守卫违例（HTTP 409 对应）。"""


class EventSink(Protocol):
    """bus 最小面（web.events.EventBus 满足；未来 Redis backend 同样满足）。"""

    def publish(self, channel: str, event: dict) -> None: ...

    def close(self, channel: str) -> None: ...


class TaskRunContext:
    """executor 运行上下文：repo 供执行期落库/复用公共存储，emit 推 SSE 进度帧，
    cancel_event 供协作式停止。"""

    def __init__(self, kit: DomainTaskKit, task_id: str, spec: dict,
                 repo: Any) -> None:
        self.task_id = task_id
        self.spec = spec
        self.repo = repo
        self.cancel_event = asyncio.Event()
        self._kit = kit

    def emit(self, event: dict) -> None:
        """上报域自定义进度帧（type 固定 progress，域字段并入）。"""
        self._kit._publish(self.task_id, {"type": "progress", **event})

    def emit_raw(self, event: dict) -> None:
        """原样发布引擎/适配器事件帧（自带 type，如套件执行进度）。"""
        self._kit._publish(self.task_id, event)


class TaskExecutor(ABC):
    """域执行器：四域各实现一份，由 DomainTaskKit 驱动。"""

    @abstractmethod
    async def execute(self, ctx: TaskRunContext) -> dict:
        """执行任务并返回 result payload；长任务经 ctx.emit 上报进度，
        轮询 ctx.cancel_event 响应停止。"""

    async def cancel(self, ctx: TaskRunContext) -> None:
        """协作式停止钩子：缺省仅置位 cancel_event；子进程型工具须在此
        kill 进程组（产物照常落盘，文件契约不受影响）。"""
        ctx.cancel_event.set()


class DomainTaskKit:
    """公共任务框架：CRUD + run/stop/retry + SSE 频道 + stale 恢复。

    进程内 _handles/_contexts 只是运行时句柄；权威状态在任务表（原则 8），
    进程重启后中间态由 mark_stale 收敛。repo 由调用方传入（Web 侧长连接、
    CLI 侧 repo_session），TaskKit 不持有连接。
    """

    def __init__(self, domain: str, table: type[Any], executor: TaskExecutor,
                 bus: EventSink) -> None:
        self.domain = domain
        self.table = table
        self.executor = executor
        self.bus = bus
        self.stop_timeout = STOP_TIMEOUT_S
        self._handles: dict[str, asyncio.Task] = {}
        self._contexts: dict[str, TaskRunContext] = {}

    # -------------------------------------------------------------- SSE 频道
    def channel(self, task_id: str) -> str:
        return f"{self.domain}:tasks:{task_id}"

    def _publish(self, task_id: str, event: dict) -> None:
        self.bus.publish(self.channel(task_id), event)

    # ------------------------------------------------------------------- CRUD
    async def create(self, repo: Any, spec: dict, name: str = "") -> dict:
        return await repo.create_domain_task(self.table, spec, name=name)

    async def list(self, repo: Any, *, status: str | None = None,
                   retry_of: str | None = None, limit: int = 50) -> list[dict]:
        return await repo.list_domain_tasks(self.table, status=status,
                                            retry_of=retry_of, limit=limit)

    async def get(self, repo: Any, task_id: str) -> dict | None:
        return await repo.get_domain_task(self.table, task_id)

    async def update(self, repo: Any, task_id: str, *, name: str | None = None,
                     spec: dict | None = None) -> dict:
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if spec is not None:
            fields["spec"] = spec
        updated = await repo.update_domain_task(self.table, task_id,
                                                expect_status=TASK_PENDING, **fields)
        if updated is None:
            raise TaskStateError("update 仅限 pending 任务（或任务不存在）")
        return updated

    async def delete(self, repo: Any, task_id: str) -> None:
        current = await self._require(repo, task_id)
        if current["status"] not in TASK_TERMINAL:
            raise TaskStateError(f"delete 仅限终态任务，当前 {current['status']}")
        await repo.delete_domain_task(self.table, task_id)

    # -------------------------------------------------------- run/stop/retry
    async def run(self, repo: Any, task_id: str) -> dict:
        await self._require(repo, task_id)
        started = await repo.update_domain_task(
            self.table, task_id, expect_status=TASK_PENDING,
            status=TASK_RUNNING, started_at=utcnow())
        if started is None:
            raise TaskStateError("run 仅限 pending 任务（或任务不存在）")
        ctx = TaskRunContext(self, task_id, dict(started["spec"]), repo)
        self._contexts[task_id] = ctx
        self._handles[task_id] = asyncio.create_task(self._drive(repo, ctx))
        self._publish(task_id, {"type": "started", "task_id": task_id})
        return started

    async def stop(self, repo: Any, task_id: str) -> dict:
        current = await self._require(repo, task_id)
        if current["status"] == TASK_STOPPING:
            return current                                      # 幂等
        if current["status"] != TASK_RUNNING:
            raise TaskStateError(f"stop 仅限 running 任务，当前 {current['status']}")
        stopping = await repo.update_domain_task(
            self.table, task_id, expect_status=TASK_RUNNING, status=TASK_STOPPING)
        if stopping is None:            # 竞态：已自然终态或已被置 stopping，以 DB 为准
            return await self._require(repo, task_id)
        self._publish(task_id, {"type": "progress", "note": "stopping"})
        ctx = self._contexts.get(task_id)
        timed_out = False
        if ctx is not None:
            try:
                await asyncio.wait_for(self.executor.cancel(ctx), timeout=self.stop_timeout)
            except (TimeoutError, asyncio.TimeoutError):
                timed_out = True
                logger.warning("stop 超时强转 cancelled: %s/%s", self.domain, task_id)
        handle = self._handles.get(task_id)
        if handle is not None:
            if not handle.done():
                handle.cancel()
            # _drive 兜底吞了业务异常；此处仅吸收取消与极端情况下的漏网异常
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await handle
        fields: dict[str, Any] = {"status": TASK_CANCELLED, "finished_at": utcnow()}
        if timed_out:
            fields["error"] = "stop timed out; cancelled forcibly"
        finished = await repo.update_domain_task(
            self.table, task_id, expect_status=TASK_STOPPING, **fields)
        if finished is not None:
            self._publish(task_id, {"type": "cancelled"})
        self.bus.close(self.channel(task_id))
        return finished or await self._require(repo, task_id)

    async def retry(self, repo: Any, task_id: str) -> dict:
        """重试 = 新任务实例（复制 spec，记 retry_of 链），不改原记录。"""
        current = await self._require(repo, task_id)
        if current["status"] not in TASK_TERMINAL:
            raise TaskStateError(f"retry 仅限终态任务，当前 {current['status']}")
        return await repo.create_domain_task(
            self.table, dict(current["spec"]), name=current["name"],
            retry_of=task_id, retry_count=current["retry_count"] + 1)

    async def wait(self, repo: Any, task_id: str) -> dict | None:
        """等待在途执行协程结束并返回最新任务态（CLI 同步形态用）。"""
        handle = self._handles.get(task_id)
        if handle is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await handle
        return await repo.get_domain_task(self.table, task_id)

    async def mark_stale(self, repo: Any) -> int:
        """startup 调用：running/stopping 残留 → stale（pending 排队任务保留）。"""
        return await repo.mark_stale_domain_tasks(self.table)

    # ------------------------------------------------------------------ 内部
    async def _require(self, repo: Any, task_id: str) -> dict:
        current = await repo.get_domain_task(self.table, task_id)
        if current is None:
            raise TaskNotFoundError(task_id)
        return current

    async def _drive(self, repo: Any, ctx: TaskRunContext) -> None:
        """执行协程外壳：终态写入 + 终态帧 + 句柄清理，唯一出口收口。

        频道关闭由终态写入方负责（succeeded/failed 此处关，cancelled 由 stop()
        关）——保证终态帧先于 end 帧，SSE 消费者不会漏收终态。进程关闭场景的
        取消（无 stop）不落状态也不关频道，交 startup mark_stale 收敛。
        """
        task_id = ctx.task_id
        try:
            result = await self.executor.execute(ctx)
            updated = await repo.update_domain_task(
                self.table, task_id, expect_status=TASK_RUNNING,
                status=TASK_SUCCEEDED, result=result, finished_at=utcnow())
            if updated is not None:     # None：状态权已易主（stop/stale），不覆盖
                self._publish(task_id, {"type": "succeeded", "result": result})
                self.bus.close(self.channel(task_id))
        except asyncio.CancelledError:
            # stop() 落 cancelled 并关频道；进程关闭场景交给 startup mark_stale 收敛
            raise
        except Exception as exc:
            await repo.update_domain_task(
                self.table, task_id, expect_status=TASK_RUNNING,
                status=TASK_FAILED, error=str(exc)[:2000], finished_at=utcnow())
            self._publish(task_id, {"type": "failed", "error": str(exc)})
            self.bus.close(self.channel(task_id))
        finally:
            self._handles.pop(task_id, None)
            self._contexts.pop(task_id, None)
