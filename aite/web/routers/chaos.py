"""可靠性框架 REST（模块 I）：发现层 / 武器目录 / 故障注入+观测编排。

- GET  /chaos/targets   发现层：节点>容器>进程 模糊查询树视图
- GET  /chaos/weapons   注入层：武器目录（注入器 × 故障类型）
- POST /chaos/observe   观测层：注入故障 + 探针流量时间线 + RTO（同步阻塞至
                        duration 结束；duration 上限 600s 防长请求占死连接）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from aite.chaos.discovery import discover_targets
from aite.chaos.injectors.base import list_weapons
from aite.service.reliability_tasks import run_observe

router = APIRouter(tags=["chaos"])


@router.get("/chaos/targets")
async def chaos_targets(node: str = "", node_ip: str = "", container: str = "",
                        container_ip: str = "", process: str = ""):
    """发现层：按节点名/节点IP/容器名/容器IP/进程名 模糊查询 → 树视图。"""
    return discover_targets({"node": node, "node_ip": node_ip,
                             "container": container, "container_ip": container_ip,
                             "process": process})


@router.get("/chaos/weapons")
async def chaos_weapons():
    """注入层武器目录：注入器 × 支持的故障类型。"""
    return {"items": list_weapons(), "count": len(list_weapons())}


@router.post("/chaos/observe")
async def chaos_observe(body: dict[str, Any]):
    """故障注入 + 背景推理观测：流式探针按 interval 打点，返回时间线与 RTO。

    body：{target: {base_url, model, api_key_env?}, faults: [FaultSpec...],
    interval?, duration?, prompt?, post_window?}
    与 reliability 域任务 spec 同构（service/reliability_tasks.run_observe）。
    """
    try:
        return await run_observe(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
