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
STOP_POLL_INTERVAL_S = 2.0         # 跨进程停止桥的 DB 轮询间隔


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
                 repo: Any, history: list[dict] | None = None) -> None:
        self.task_id = task_id
        self.spec = spec
        self.repo = repo
        self.history = history or []   # 本轮开启的执行历史（终态收口用）
        self.run_id: str | None = None  # 引擎建 run 行后经 run_cb 回填（失败/取消轮也要有）
        self.run_cb: Any = None         # executor 透传给执行引擎：建 run 行即回调
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
        self.stop_poll_interval = STOP_POLL_INTERVAL_S
        self._handles: dict[str, asyncio.Task] = {}
        self._contexts: dict[str, TaskRunContext] = {}
        self._watchers: dict[str, asyncio.Task] = {}

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
        """改 name/spec：非运行中即可改（pending 改首发配置，终态改完可重跑）。"""
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if spec is not None:
            fields["spec"] = spec
        updated = await repo.update_domain_task(
            self.table, task_id,
            expect_status=(TASK_PENDING, *TASK_TERMINAL), **fields)
        if updated is None:
            raise TaskStateError("update 仅限非运行中任务（运行中/停止中不可改）")
        return updated

    async def delete(self, repo: Any, task_id: str) -> None:
        current = await self._require(repo, task_id)
        if current["status"] not in TASK_TERMINAL:
            raise TaskStateError(f"delete 仅限终态任务，当前 {current['status']}")
        await repo.delete_domain_task(self.table, task_id)

    # -------------------------------------------------------- run/stop/retry
    @staticmethod
    def _open_round(current: dict, trigger: str) -> list[dict]:
        """开新一轮执行历史：快照当时 spec（审计：每轮结果对应每轮配置）。"""
        history = [dict(r) for r in (current.get("history") or [])]
        history.append({"n": len(history) + 1, "trigger": trigger,
                        "spec": dict(current.get("spec") or {}),
                        "status": TASK_RUNNING,
                        "started_at": utcnow().isoformat()})
        return history

    @staticmethod
    def _close_round(history: list[dict] | None, status: str,
                     result: dict | None = None, error: str | None = None,
                     run_id: str | None = None) -> dict | None:
        """收最后一轮（状态机保证单任务同时只有一个在途运行，无并发写）。

        run_id：失败/取消路径经 ctx.run_id 补记（成功路径随 result 带回）。"""
        if not history:
            return None                     # 旧数据无历史列，维持原样
        rounds = [dict(r) for r in history]
        last = rounds[-1]
        last["status"] = status
        last["finished_at"] = utcnow().isoformat()
        if error:
            last["error"] = error[:2000]
        if run_id and not last.get("run_id"):
            last["run_id"] = run_id
        if result:
            for key in ("run_id", "pass_rate", "total_cases", "evaluated_cases"):
                if result.get(key) is not None:
                    last[key] = result[key]
        return rounds

    async def run(self, repo: Any, task_id: str, *, trigger: str = "run") -> dict:
        """运行：pending 可首发、终态可重跑（同一任务，追加执行历史轮次）。"""
        current = await self._require(repo, task_id)
        started = await repo.update_domain_task(
            self.table, task_id, expect_status=(TASK_PENDING, *TASK_TERMINAL),
            status=TASK_RUNNING, started_at=utcnow(),
            result=None, error=None, finished_at=None,
            history=self._open_round(current, trigger))
        if started is None:
            raise TaskStateError("run 仅限 pending 或终态任务（运行中不可重复发起）")
        ctx = TaskRunContext(self, task_id, dict(started["spec"]), repo,
                             started.get("history"))

        async def _note_run_id(run_id: str) -> None:
            """引擎建 run 行即回填本轮 run_id（失败/取消轮收口也能对上留痕）。"""
            ctx.run_id = run_id
            current = await repo.get_domain_task(self.table, task_id)
            hist = [dict(r) for r in (current or {}).get("history") or []]
            if hist and not hist[-1].get("run_id"):
                hist[-1]["run_id"] = run_id
                await repo.update_domain_task(self.table, task_id,
                                              expect_status=TASK_RUNNING,
                                              history=hist)

        ctx.run_cb = _note_run_id
        self._contexts[task_id] = ctx
        self._handles[task_id] = asyncio.create_task(self._drive(repo, ctx))
        self._watchers[task_id] = asyncio.create_task(self._watch_stop(repo, ctx))
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
        await self._finish_cancelled(
            repo, task_id, ctx=ctx,
            error="stop timed out; cancelled forcibly" if timed_out else None)
        return await self._require(repo, task_id)

    async def _watch_stop(self, repo: Any, ctx: TaskRunContext) -> None:
        """跨进程停止桥：CLI/Web 各自独立进程，stop 请求落在 DB 的 stopping
        状态上；执行进程轮询发现后触发协作式 cancel 并落 cancelled 终态。
        子进程型引擎的进程组强杀由各域 executor.cancel 补强（C1-C4）。"""
        task_id = ctx.task_id
        try:
            while True:
                await asyncio.sleep(self.stop_poll_interval)
                current = await repo.get_domain_task(self.table, task_id)
                if current is None or current["status"] not in (TASK_RUNNING,
                                                                TASK_STOPPING):
                    return                      # 已易主/终态：出口归属在写入方
                if current["status"] != TASK_STOPPING:
                    continue
                ctx.cancel_event.set()
                try:
                    await asyncio.wait_for(self.executor.cancel(ctx),
                                           timeout=self.stop_timeout)
                except (TimeoutError, asyncio.TimeoutError):
                    logger.warning("跨进程 stop 的 cancel 超时: %s/%s",
                                   self.domain, task_id)
                handle = self._handles.get(task_id)
                if handle is not None and not handle.done():
                    handle.cancel()
                await self._finish_cancelled(repo, task_id, ctx=ctx)
                return
        except asyncio.CancelledError:
            raise
        finally:
            self._watchers.pop(task_id, None)

    async def _finish_cancelled(self, repo: Any, task_id: str,
                                ctx: TaskRunContext | None = None, *,
                                error: str | None = None) -> None:
        """stopping → cancelled 终态落库 + cancelled 帧 + 关频道（唯一出口）。
        expect 守卫下多调用方（本进程 stop / 跨进程轮询桥）竞态安全；
        执行进程内 ctx 必有（桥从 _contexts 取），收轮时用它补本轮 run_id。"""
        fields: dict[str, Any] = {"status": TASK_CANCELLED, "finished_at": utcnow()}
        if error:
            fields["error"] = error
        current = await repo.get_domain_task(self.table, task_id)
        closed = self._close_round((current or {}).get("history"),
                                   TASK_CANCELLED, error=error,
                                   run_id=ctx.run_id if ctx else None)
        if closed:
            fields["history"] = closed
        finished = await repo.update_domain_task(
            self.table, task_id, expect_status=TASK_STOPPING, **fields)
        if finished is not None:
            self._publish(task_id, {"type": "cancelled"})
        self.bus.close(self.channel(task_id))

    async def retry(self, repo: Any, task_id: str) -> dict:
        """重试 = 终态任务同配置再跑一轮（同一任务，追加执行历史轮次）。

        不再新建同级任务（owner 2026-11-12 拍板）：一个任务的多次执行是它的
        历史，不是多个任务。改参重跑走 PATCH（终态可改）+ run；旧 retry_of
        链字段保留，仅用于读取历史数据。
        """
        current = await self._require(repo, task_id)
        if current["status"] not in TASK_TERMINAL:
            raise TaskStateError(f"retry 仅限终态任务，当前 {current['status']}")
        return await self.run(repo, task_id, trigger="retry")

    async def duplicate(self, repo: Any, task_id: str) -> dict:
        """复制 = 开分支变体：同 spec 新建 pending 任务（任意状态可用）。

        与 retry 的分工：retry 是同一任务的历史轮次；复制是独立的新任务
        （可 PATCH 改 spec 后再 run），名字加「· 副本」后缀便于列表区分。
        """
        current = await self._require(repo, task_id)
        name = f"{current['name']} · 副本" if current["name"] else "副本任务"
        return await repo.create_domain_task(self.table, dict(current["spec"]),
                                             name=name)

    async def wait(self, repo: Any, task_id: str) -> dict | None:
        """等待在途执行协程（及跨进程停止桥）结束并返回最新任务态（CLI 同步形态用）。"""
        for bag in (self._handles, self._watchers):
            t = bag.get(task_id)
            if t is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
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
                status=TASK_SUCCEEDED, result=result, finished_at=utcnow(),
                history=self._close_round(ctx.history, TASK_SUCCEEDED,
                                          result=result))
            if updated is not None:     # None：状态权已易主（stop/stale），不覆盖
                self._publish(task_id, {"type": "succeeded", "result": result})
                self.bus.close(self.channel(task_id))
        except asyncio.CancelledError:
            # stop() 落 cancelled 并关频道；进程关闭场景交给 startup mark_stale 收敛
            raise
        except Exception as exc:
            await repo.update_domain_task(
                self.table, task_id, expect_status=TASK_RUNNING,
                status=TASK_FAILED, error=str(exc)[:2000], finished_at=utcnow(),
                history=self._close_round(ctx.history, TASK_FAILED, error=str(exc),
                                          run_id=ctx.run_id))
            self._publish(task_id, {"type": "failed", "error": str(exc)})
            self.bus.close(self.channel(task_id))
        finally:
            self._handles.pop(task_id, None)
            self._contexts.pop(task_id, None)
