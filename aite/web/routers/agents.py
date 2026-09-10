"""Agents: 执行机注册/心跳派发/结果回传（模块 G，拉取式 agent 通道）。

agent 只做出站 HTTP：注册 → 心跳（响应携带派发任务）→ 执行 → 回传。
所有端点走全局 auth_guard（配置 AITE_WEB_TOKEN 时 agent 需带同一 token）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aite.service import agent_service
from aite.web.deps import get_state

router = APIRouter(tags=["agents"])


class RegisterIn(BaseModel):
    name: str
    labels: list[str] = []
    version: str | None = None


class HeartbeatIn(BaseModel):
    current_task_id: str | None = None


class AgentTaskIn(BaseModel):
    config: dict[str, Any]           # suite raw（含 engine）
    node: str | None = None          # 执行机名称或 id（缺省 = 任意空闲节点认领）


class ResultIn(BaseModel):
    payload: dict[str, Any]          # {status, rows, raw_score, ..., timeseries}


@router.post("/agents/register")
async def register(body: RegisterIn, state: Any = Depends(get_state)):
    await agent_service.sweep_stale(state.repo)
    agent = await state.repo.register_agent(body.name, body.labels, body.version)
    return agent


@router.post("/agents/{agent_id}/heartbeat")
async def heartbeat(agent_id: str, body: HeartbeatIn,
                    state: Any = Depends(get_state)):
    """心跳即派发：响应携带认领到的任务（无任务则 null），免独立拉取端点。"""
    node = await state.repo.heartbeat_agent(agent_id, body.current_task_id)
    if node is None:
        raise HTTPException(404, "agent not registered")
    task = await state.repo.claim_agent_task(agent_id)
    return {"heartbeat": "ok", "task": task}


@router.get("/agents")
async def list_agents(state: Any = Depends(get_state)):
    await agent_service.sweep_stale(state.repo)
    items = await state.repo.list_agents()
    return {"items": items, "count": len(items)}


@router.post("/agent-tasks")
async def create_agent_task(body: AgentTaskIn, state: Any = Depends(get_state)):
    try:
        return await agent_service.dispatch_task(state.repo, body.config, body.node)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/agent-tasks")
async def list_agent_tasks(state: Any = Depends(get_state), limit: int = 50):
    await agent_service.sweep_stale(state.repo)
    items = await state.repo.list_agent_tasks(limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/agent-tasks/{task_id}")
async def get_agent_task(task_id: str, state: Any = Depends(get_state)):
    task = await state.repo.get_agent_task(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


@router.post("/agent-tasks/{task_id}/result")
async def agent_task_result(task_id: str, body: ResultIn,
                            state: Any = Depends(get_state)):
    """agent 回传结果：master 重建产出目录并走统一入库链路。"""
    task = await state.repo.get_agent_task(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    if task["status"] not in ("dispatched", "running"):
        raise HTTPException(409, f"task in non-receivable state: {task['status']}")
    run = await agent_service.receive_result(state.repo, task, body.payload)
    return {"task_id": task_id, **run}
