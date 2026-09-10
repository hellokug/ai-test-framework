"""Background tasks (run launches) + platform stats."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aite.service.web_service import compute_platform_stats
from aite.web.deps import get_state

router = APIRouter(tags=["tasks"])


@router.get("/tasks")
async def list_tasks(state: Any = Depends(get_state), limit: int = 50):
    items = await state.repo.list_bg_tasks(limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, state: Any = Depends(get_state)):
    task = await state.repo.get_bg_task(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


@router.get("/stats")
async def stats(state: Any = Depends(get_state)):
    runs = await state.repo.list_runs(limit=500)
    return compute_platform_stats(runs, active_tasks=len(state.tasks))
