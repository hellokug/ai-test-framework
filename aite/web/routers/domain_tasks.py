"""四域任务 REST（docs/domain-backends.md §2 契约的 REST 切片）。

四域契约同形 → 一份路由工厂 + 四个实例（原则 3）；kit 由 AppState.task_kits
提供（启动装配、bus 共享 SSE 频道），业务规则全在 core/taskkit + service 层
（原则 1：路由只做参数校验 → 调 kit → 错误映射 → 返回）。错误语义显式化：
TaskNotFoundError → 404、TaskValidationError（任务名必填）→ 422、
TaskStateError（状态机守卫）→ 409（子类 TaskNameConflictError = 同域同名占用）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from aite.core.taskkit import DomainTaskKit, TaskNotFoundError, TaskStateError, TaskValidationError
from aite.core.types import TASK_TERMINAL, utcnow
from aite.web.deps import get_state
from aite.web.sse import stream_events


class TaskCreateIn(BaseModel):
    name: str   # 必填（缺失 → pydantic 422；空/纯空白 → kit 422）；同域唯一（占用 → 409）
    spec: dict[str, Any]
    # 创建 ≠ 执行（V1-PLAN §8.5）：{mode: now|scheduled|manual, at?}；缺省 = 仅创建
    execute: dict[str, Any] | None = None


class TaskUpdateIn(BaseModel):
    name: str | None = None
    spec: dict[str, Any] | None = None


class TaskStopIn(BaseModel):
    mode: str = "graceful"   # graceful=立即停止（排空在途）；force=强制停止（在途即弃）


def _kit(state: Any, domain: str) -> DomainTaskKit:
    kit = state.task_kits.get(domain)
    if kit is None:  # pragma: no cover - 四域在启动装配，防御性兜底
        raise HTTPException(404, f"unknown domain: {domain}")
    return kit


async def _call(op, *args, **kwargs):
    """kit 调用 → HTTP 语义统一映射（404/409）。"""
    try:
        return await op(*args, **kwargs)
    except TaskNotFoundError as exc:
        raise HTTPException(404, f"task not found: {exc}") from exc
    except TaskValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except TaskStateError as exc:   # 含子类 TaskNameConflictError（同名占用 409）
        raise HTTPException(409, str(exc)) from exc


_EXECUTE_MODES = ("now", "scheduled", "manual")


def _parse_scheduled_at(execute: dict[str, Any] | None) -> tuple[str, datetime | None]:
    """execute 键解析（V1-PLAN §8.5「创建 ≠ 执行」）：返回 (mode, scheduled_at)。

    缺省/None → manual（仅创建，兼容旧两段式前端）；now → 创建后立即 run；
    scheduled → at 必填（ISO 时间，无时区按服务器本地时区解释）且须为未来
    时间，统一转 UTC 存储（调度比较与 created_at 同口径）。校验失败抛
    TaskValidationError（调用方映射 422）。"""
    if not execute:
        return "manual", None
    mode = str(execute.get("mode") or "manual")
    if mode not in _EXECUTE_MODES:
        raise TaskValidationError(
            f"execute.mode 须为 {'/'.join(_EXECUTE_MODES)} 之一，当前 {mode!r}")
    if mode != "scheduled":
        return mode, None
    raw = execute.get("at")
    if not raw:
        raise TaskValidationError("execute.mode=scheduled 需提供 at（ISO 时间）")
    try:
        at = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise TaskValidationError(f"execute.at 非法（须 ISO 时间）：{raw}") from exc
    if at.tzinfo is None:
        at = at.astimezone()               # naive 视为服务器本地时区
    at = at.astimezone(timezone.utc)
    if at <= utcnow():
        raise TaskValidationError(f"execute.at 已过期：{raw}")
    return mode, at


def build_domain_tasks_router(domain: str) -> APIRouter:
    """按域名构建同形任务路由（POST /{D}/tasks … GET /{D}/tasks/{id}/stream）。"""
    router = APIRouter(tags=[f"{domain}-tasks"])
    base = f"/{domain}/tasks"

    @router.post(base)
    async def create_task(body: TaskCreateIn, state: Any = Depends(get_state)):
        try:
            mode, sched_at = _parse_scheduled_at(body.execute)
        except TaskValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        task = await _call(_kit(state, domain).create, state.repo,
                           body.spec, body.name, scheduled_at=sched_at)
        if mode == "now":   # 创建后立即 run（「创建 ≠ 执行」的 now 形态）
            return await _call(_kit(state, domain).run, state.repo,
                               task["id"], trigger="create")
        return task

    @router.get(base)
    async def list_tasks(state: Any = Depends(get_state),
                         status: str | None = None, retry_of: str | None = None,
                         limit: int = 50):
        items = await _call(_kit(state, domain).list, state.repo, status=status,
                            retry_of=retry_of, limit=limit)
        return {"items": items, "count": len(items)}

    @router.get(f"{base}/{{task_id}}")
    async def get_task(task_id: str, state: Any = Depends(get_state)):
        task = await _call(_kit(state, domain).get, state.repo, task_id)
        if task is None:
            raise HTTPException(404, f"task not found: {task_id}")
        return task

    @router.patch(f"{base}/{{task_id}}")
    async def patch_task(task_id: str, body: TaskUpdateIn,
                         state: Any = Depends(get_state)):
        # kit.update 对不存在与运行中同返 409；契约要求 404 先行区分
        if await _call(_kit(state, domain).get, state.repo, task_id) is None:
            raise HTTPException(404, f"task not found: {task_id}")
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        return await _call(_kit(state, domain).update, state.repo, task_id,
                           **fields)

    @router.delete(f"{base}/{{task_id}}")
    async def delete_task(task_id: str, state: Any = Depends(get_state)):
        await _call(_kit(state, domain).delete, state.repo, task_id)
        return {"deleted": True}

    @router.post(f"{base}/{{task_id}}/run", status_code=202)
    async def run_task(task_id: str, state: Any = Depends(get_state)):
        """异步执行：pending 首发 / 终态重跑（同一任务追加历史轮次），
        立即返回任务快照（running 态），进度走 SSE。"""
        return await _call(_kit(state, domain).run, state.repo, task_id)

    @router.post(f"{base}/{{task_id}}/stop")
    async def stop_task(task_id: str, state: Any = Depends(get_state),
                        body: TaskStopIn | None = None):
        """发起停止立即返回：graceful=排空在途收口；force=杀树在途即弃。
        终态经 SSE cancelled 帧 / 轮询可见。"""
        return await _call(_kit(state, domain).stop, state.repo, task_id,
                           mode=body.mode if body else "graceful")

    @router.post(f"{base}/{{task_id}}/retry", status_code=202)
    async def retry_task(task_id: str, state: Any = Depends(get_state)):
        """重试 = 终态任务同配置再跑一轮（同一任务，追加执行历史轮次）。"""
        return await _call(_kit(state, domain).retry, state.repo, task_id)

    @router.post(f"{base}/{{task_id}}/duplicate", status_code=201)
    async def duplicate_task(task_id: str, state: Any = Depends(get_state)):
        """复制 = 同 spec 新建 pending 任务（任意状态可用，不带 retry 链）。"""
        return await _call(_kit(state, domain).duplicate, state.repo, task_id)

    @router.get(f"{base}/{{task_id}}/result")
    async def get_result(task_id: str, state: Any = Depends(get_state)):
        task = await _call(_kit(state, domain).get, state.repo, task_id)
        if task is None:
            raise HTTPException(404, f"task not found: {task_id}")
        if task["status"] not in TASK_TERMINAL:
            raise HTTPException(409, f"task not terminal: {task['status']}")
        return {"task_id": task_id, "status": task["status"],
                "result": task["result"], "error": task["error"]}

    @router.get(f"{base}/{{task_id}}/stream")
    async def stream_task(task_id: str, request: Request,
                          state: Any = Depends(get_state)):
        return stream_events(state.bus, request, _kit(state, domain).channel(task_id))

    return router


functional_tasks = build_domain_tasks_router("functional")
accuracy_tasks = build_domain_tasks_router("accuracy")
perf_tasks = build_domain_tasks_router("perf")
reliability_tasks = build_domain_tasks_router("reliability")

# 模块级聚合（app.py 统一 include_router(router.router) 惯例不变）
router = APIRouter(tags=["domain-tasks"])
for _sub in (functional_tasks, accuracy_tasks, perf_tasks, reliability_tasks):
    router.include_router(_sub)
