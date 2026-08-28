"""Historical trends over run_metric_summary (denormalized, single-table scan)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from aite.web.deps import get_state

router = APIRouter(tags=["trends"])


@router.get("/trends")
async def trends(state: Any = Depends(get_state), suite: str | None = None,
                 metric: str | None = None, days: int = 30):
    rows = await state.repo.trends(run_name=suite, metric=metric, days=days)
    return {"items": rows, "count": len(rows)}
