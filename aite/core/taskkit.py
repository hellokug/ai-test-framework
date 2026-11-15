"""公共任务框架 TaskKit：四域独立任务管理的单一实现（docs/domain-backends.md）。

一份代码、四份数据——每域一张任务表 + 一个 TaskExecutor 实现，DomainTaskKit
实例化驱动统一状态机：pending → running → stopping → cancelled，或
running → succeeded / failed；running/stopping 残留由 startup mark_stale 收敛。
状态常量单一来源 core.types（原则 8）；DB 访问全部经 repository 泛型方法（原则 6）；
事件通道走 EventSink 协议（web.events.EventBus 满足），core 不依赖 web（原则 2）。

状态转移一律走 repo 的 expect_status 条件更新：终态写入方以「先到者得」裁决
（原则 8 唯一出口归属），后到者重读 DB 以 DB 为准，不覆盖。

**停止语义（两段式，2026-11-15 性能域立即/强制停止改造）**：

- ``stop(mode="graceful")`` 立即停止：落 stopping（含 stop_mode 入执行历史轮）
  并置位 ctx.cancel_event 后**立即返回**——等待不占用 HTTP 请求。执行器收到
  cancel_event 自行协作收尾（perf：停止派发新请求、排空在途后正常收集），
  ``_drive`` 发现执行返回时已处于 stopping → 以 cancelled 收口（历史轮带
  result keys，run_id 可追溯部分产物）。
- ``stop(mode="force")`` 强制停止：置位 ctx.force_event——子进程型引擎直接
  杀进程树，在途请求即弃；已完成请求经逐请求落库（perf benchmark_data.db）
  兜底收集，未返回请求不计入统计。
- ``_watch_stop`` 跨进程桥：轮询发现 DB stopping 后按历史轮 stop_mode 补置
  事件，并等待执行协程自行收口；超 stop_timeout 未完 → 升级 force_event，
  再宽限 STOP_FORCE_GRACE_S 仍卡死才 cancel 句柄兜底，保证 stop 必达。
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

STOP_TIMEOUT_S = 60.0              # 桥等执行器自行收口的上限，超时升级 force
STOP_FORCE_GRACE_S = 30.0          # 升级 force 后再宽限的秒数，仍卡死才 cancel 句柄
STOP_POLL_INTERVAL_S = 2.0         # 跨进程停止桥的 DB 轮询间隔
STOP_MODES = ("graceful", "force")  # graceful=立即停止（排空在途）；force=强制停止


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
    cancel_event 供立即停止（协作排空），force_event 供强制停止（在途即弃）。"""

    def __init__(self, kit: DomainTaskKit, task_id: str, spec: dict,
                 repo: Any, history: list[dict] | None = None) -> None:
        self.task_id = task_id
        self.spec = spec
        self.repo = repo
        self.history = history or []   # 本轮开启的执行历史（终态收口用）
        self.run_id: str | None = None  # 引擎建 run 行后经 run_cb 回填（失败/取消轮也要有）
        self.run_cb: Any = None         # executor 透传给执行引擎：建 run 行即回调
        self.cancel_event = asyncio.Event()
        self.force_event = asyncio.Event()   # 强制停止：子进程型引擎直接杀树，在途即弃
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
        轮询 ctx.cancel_event / ctx.force_event 响应立即/强制停止。"""

    async def cancel(self, ctx: TaskRunContext) -> None:
        """停止请求钩子（stop/桥置事件后调用，须快速返回）：缺省仅置位
        cancel_event；需要额外停止侧清理的执行器在此扩展。子进程型引擎的
        进程组强杀走 ctx.force_event 协作协议（适配器监督循环自行杀树），
        不再在钩子里阻塞等待。"""
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

    async def stop(self, repo: Any, task_id: str, *,
                   mode: str = "graceful") -> dict:
        """发起停止：graceful=立即停止（停止派发新请求，排空在途后收口），
        force=强制停止（在途请求直接取消/杀树，未返回请求不计入统计）。

        只落 stopping（stop_mode 记入本轮执行历史，供跨进程桥识别）并置位
        对应事件后**立即返回**——不阻塞等待执行收尾，等待由 _watch_stop 桥
        承担（终态经 SSE cancelled 帧通知）。幂等：stopping 中再次 stop 允许
        graceful → force 升级。"""
        if mode not in STOP_MODES:
            raise TaskStateError(f"stop mode 须为 {' / '.join(STOP_MODES)}，收到 {mode}")
        current = await self._require(repo, task_id)
        if current["status"] == TASK_STOPPING:
            await self._escalate(repo, task_id, current, mode)
            return await self._require(repo, task_id)
        if current["status"] != TASK_RUNNING:
            raise TaskStateError(f"stop 仅限 running 任务，当前 {current['status']}")
        stopping = await repo.update_domain_task(
            self.table, task_id, expect_status=TASK_RUNNING, status=TASK_STOPPING,
            history=self._mark_stop_mode(current.get("history"), mode))
        if stopping is None:            # 竞态：已自然终态或已被置 stopping，以 DB 为准
            return await self._require(repo, task_id)
        self._publish(task_id, {"type": "progress", "note": "stopping", "mode": mode})
        ctx = self._contexts.get(task_id)
        if ctx is not None:
            self._set_stop_event(ctx, mode)
            if mode == "graceful":
                try:
                    await asyncio.wait_for(self.executor.cancel(ctx), timeout=5.0)
                except (TimeoutError, asyncio.TimeoutError):
                    logger.warning("stop 钩子超时（忽略，事件已置位）: %s/%s",
                                   self.domain, task_id)
        return stopping

    @staticmethod
    def _mark_stop_mode(history: list[dict] | None, mode: str) -> list[dict] | None:
        """本轮执行历史标记停止模式（跨进程桥据此识别 graceful/force）。"""
        rounds = [dict(r) for r in (history or [])]
        if rounds:
            rounds[-1]["stop_mode"] = mode
        return rounds

    @staticmethod
    def _last_stop_mode(current: dict) -> str:
        hist = current.get("history") or []
        return (hist[-1].get("stop_mode") if hist else None) or "graceful"

    @staticmethod
    def _set_stop_event(ctx: TaskRunContext, mode: str) -> None:
        (ctx.force_event if mode == "force" else ctx.cancel_event).set()

    async def _escalate(self, repo: Any, task_id: str, current: dict,
                        mode: str) -> None:
        """stopping 中升级 stop 模式（graceful → force）：置事件 + 补历史标记。"""
        if mode != "force":
            return
        ctx = self._contexts.get(task_id)
        if ctx is not None:
            ctx.force_event.set()
        with contextlib.suppress(Exception):
            await repo.update_domain_task(
                self.table, task_id, expect_status=TASK_STOPPING,
                history=self._mark_stop_mode(current.get("history"), mode))

    async def _watch_stop(self, repo: Any, ctx: TaskRunContext) -> None:
        """跨进程停止桥：CLI/Web 各自独立进程，stop 请求落在 DB 的 stopping
        状态上；执行进程轮询发现后按 stop_mode 置事件，并等待执行协程自行
        收口（_drive 在 stopping 下以 cancelled 收口，产物已收集）；超时升级
        force_event，再宽限仍卡死才 cancel 句柄兜底——stop 必达。"""
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
                self._set_stop_event(ctx, self._last_stop_mode(current))
                await self._await_executor(repo, ctx)
                return
        except asyncio.CancelledError:
            raise
        finally:
            self._watchers.pop(task_id, None)

    async def _await_executor(self, repo: Any, ctx: TaskRunContext) -> None:
        """等待执行协程自行收口（优雅排空/强制杀树后的截断收集）；stop_timeout
        未完 → 升级 force_event 再宽限 STOP_FORCE_GRACE_S，仍卡死 cancel 句柄。
        最后仍处 stopping（执行协程死于取消等未落终态的场景）→ 兜底落 cancelled。"""
        task_id = ctx.task_id
        handle = self._handles.get(task_id)
        if handle is not None and not handle.done():
            try:
                # shield：wait_for 超时只弃等待，不取消执行协程本体
                await asyncio.wait_for(asyncio.shield(handle),
                                       timeout=self.stop_timeout)
            except asyncio.CancelledError:
                raise
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("停止等待超时，升级强制停止: %s/%s",
                               self.domain, task_id)
                ctx.force_event.set()
                with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(handle),
                                           timeout=STOP_FORCE_GRACE_S)
            if not handle.done():
                handle.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await handle
        current = await repo.get_domain_task(self.table, task_id)
        if (current or {}).get("status") == TASK_STOPPING:
            await self._finish_cancelled(repo, task_id, ctx=ctx)

    async def _finish_cancelled(self, repo: Any, task_id: str,
                                ctx: TaskRunContext | None = None, *,
                                error: str | None = None,
                                result: dict | None = None) -> None:
        """stopping → cancelled 终态落库 + cancelled 帧 + 关频道（唯一出口）。
        expect 守卫下多调用方（_drive 停止竞态收口 / 桥兜底）竞态安全；
        执行进程内 ctx 必有（桥从 _contexts 取），收轮时用它补本轮 run_id。
        result：执行器已收完的产物概要（优雅排空全量 / 强制截断部分）——
        历史轮带回 run_id/total_cases 等 keys，取消轮也能追溯部分产物。"""
        fields: dict[str, Any] = {"status": TASK_CANCELLED, "finished_at": utcnow()}
        if error:
            fields["error"] = error
        current = await repo.get_domain_task(self.table, task_id)
        closed = self._close_round((current or {}).get("history"),
                                   TASK_CANCELLED, result=result, error=error,
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

        频道关闭由终态写入方负责（succeeded/failed 此处关，cancelled 由
        _finish_cancelled 关）——保证终态帧先于 end 帧，SSE 消费者不会漏收
        终态。执行返回时发现已处于 stopping（停止竞态获胜）：产物已由执行器
        收完（优雅排空全量 / 强制杀树截断），以 cancelled 收口且历史轮带
        result keys，而不是写 succeeded 覆盖停止语义。进程关闭场景的取消
        （无 stop）不落状态也不关频道，交 startup mark_stale 收敛。
        """
        task_id = ctx.task_id
        try:
            result = await self.executor.execute(ctx)
            current = await self._require(repo, task_id)
            if current["status"] == TASK_STOPPING:
                await self._finish_cancelled(repo, task_id, ctx=ctx, result=result)
                return
            updated = await repo.update_domain_task(
                self.table, task_id, expect_status=TASK_RUNNING,
                status=TASK_SUCCEEDED, result=result, finished_at=utcnow(),
                history=self._close_round(ctx.history, TASK_SUCCEEDED,
                                          result=result))
            if updated is not None:     # None：状态权已易主（stop/stale），不覆盖
                self._publish(task_id, {"type": "succeeded", "result": result})
                self.bus.close(self.channel(task_id))
        except asyncio.CancelledError:
            # stop 升级路径（桥超时强杀）由 _finish_cancelled 落 cancelled 并关
            # 频道；进程关闭场景交给 startup mark_stale 收敛
            raise
        except Exception as exc:
            # 停止竞态（stop 桥已落 stopping，执行随后抛错）：与成功路径同口径，
            # 以 cancelled 收口——failed 更新 expect RUNNING 会空转，直接写会把
            # stopping 卡成无终态（startup 收 stale，停止语义被错误覆盖）
            current = await self._require(repo, task_id)
            if current["status"] == TASK_STOPPING:
                await self._finish_cancelled(repo, task_id, ctx=ctx,
                                             error=str(exc)[:2000])
                return
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
