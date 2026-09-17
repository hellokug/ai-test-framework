"""可靠性框架 REST（模块 I）：发现层 / 武器目录 / 故障注入+观测编排。

- GET  /chaos/targets   发现层：节点>容器>进程 模糊查询树视图
- GET  /chaos/weapons   注入层：武器目录（注入器 × 故障类型）
- POST /chaos/observe   观测层：注入故障 + 探针流量时间线 + RTO（同步阻塞至
                        duration 结束；duration 上限 600s 防长请求占死连接）
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException

from aite.chaos.discovery import discover_targets
from aite.chaos.injectors.base import list_weapons
from aite.chaos.model import validate_specs
from aite.chaos.observer import ProbeConfig, observe_under_chaos

router = APIRouter(tags=["chaos"])

_MAX_OBSERVE_DURATION = 600.0     # 同步观测的时长上限（秒）


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
    """
    target = body.get("target") or {}
    base_url = str(target.get("base_url") or "").strip().rstrip("/")
    model = str(target.get("model") or "").strip()
    if not base_url or not model:
        raise HTTPException(400, "target.base_url 与 target.model 必填")
    url = base_url if base_url.endswith("/chat/completions") \
        else base_url + "/chat/completions"
    try:
        specs = validate_specs(body.get("faults") or [])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    interval = float(body.get("interval") or 5.0)
    duration = float(body.get("duration") or 60.0)
    if interval <= 0 or duration <= 0 or duration > _MAX_OBSERVE_DURATION:
        raise HTTPException(400, f"interval 须 >0；duration 须在 (0, "
                                 f"{_MAX_OBSERVE_DURATION:.0f}] 秒内")
    api_key = os.environ.get(target.get("api_key_env") or "OPENAI_API_KEY", "EMPTY")
    cfg = ProbeConfig(url=url, model=model, api_key=api_key,
                      interval=interval, duration=duration,
                      prompt=str(body.get("prompt") or "ping"))
    try:
        result = await observe_under_chaos(
            specs, cfg, post_window=body.get("post_window"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result["probe"] = {"url": url, "model": model, "interval": interval,
                       "duration": duration}
    return result
