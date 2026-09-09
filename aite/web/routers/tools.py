"""Tools: 引擎注册表与基准/场景目录（C4b 工具域 REST 切片）。

任务创建/执行不设独立端点：``POST /api/v1/runs``（config 直传 engine 块）即
路由工具层后台执行，``GET /api/v1/tasks/{id}/stream`` SSE 进度与
``GET /api/v1/runs/{id}`` 结果查询已有——本路由只补前端建任务表单的两个
目录数据源：引擎选择器与数据集/场景选择器。
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from aite.tools import get_tool, list_tools
from aite.web.deps import get_state

router = APIRouter(tags=["tools"])


@router.get("/tools/engines")
async def list_engines(state: Any = Depends(get_state)):
    """枚举已注册工具引擎（engine 字段可选值）。"""
    engines = []
    for name in list_tools():
        tasks = []
        # 目录信息尽力而为：缺可选依赖的引擎仍列出（task_count=0）
        with contextlib.suppress(Exception):
            tasks = get_tool(name).list_tasks()
        engines.append({"name": name, "task_count": len(tasks)})
    return {"items": engines, "count": len(engines)}


@router.get("/tools/benchmarks")
async def list_benchmarks(engine: str = Query(..., description="工具引擎名"),
                          state: Any = Depends(get_state)):
    """枚举引擎的基准/场景目录（tools.datasets 可选值；list_tasks 透传）。"""
    try:
        tasks = get_tool(engine).list_tasks()
    except KeyError as exc:
        raise HTTPException(404, str(exc).split(";")[0]) from None
    return {"engine": engine, "items": tasks, "count": len(tasks)}
