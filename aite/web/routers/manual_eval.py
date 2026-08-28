"""Manual evaluation loop: create tasks from run results, submit scores, export."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aite.web.deps import get_state

router = APIRouter(tags=["manual-eval"])


class TaskCreate(BaseModel):
    run_id: str | None = None
    case_result_ids: list[int] = []
    limit: int = 10
    rubric: str | None = None
    title: str = ""


class ScoreIn(BaseModel):
    score: float
    passed: bool | None = None
    reviewer: str = ""
    comment: str | None = None


@router.post("/manual-eval/tasks")
async def create_tasks(body: TaskCreate, state: Any = Depends(get_state)):
    """Create manual-eval tasks from a run's case results (or pick first N)."""
    if not body.run_id:
        raise HTTPException(422, "run_id required")
    results = await state.repo.list_case_results(body.run_id, limit=None)
    results = [r for r in results if r.get("variant", "base") == "base"]
    if body.case_result_ids:
        results = [r for r in results if r["id"] in set(body.case_result_ids)]
    else:
        results = results[: body.limit]
    if not results:
        raise HTTPException(404, "no case results found for this run")
    created = []
    for r in results:
        case = await state.repo.get_case(r["case_id"])
        task_id = await state.repo.create_manual_task({
            "run_id": body.run_id, "case_result_id": r["id"],
            "title": body.title or f"case {r.get('external_key')}",
            "case_input": (case or {}).get("input"),
            "expected": (case or {}).get("expected"),
            "output": r.get("output"), "rubric": body.rubric,
        })
        created.append(task_id)
    return {"created": len(created), "task_ids": created}


@router.get("/manual-eval/tasks")
async def list_tasks(status: str | None = None, state: Any = Depends(get_state),
                     limit: int = 100):
    items = await state.repo.list_manual_tasks(status=status, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/manual-eval/tasks/{task_id}/score")
async def submit_score(task_id: str, body: ScoreIn, state: Any = Depends(get_state)):
    ok = await state.repo.submit_manual_score(
        task_id, body.score, reviewer=body.reviewer,
        comment=body.comment, passed=body.passed)
    if not ok:
        raise HTTPException(404, "task not found")
    return {"submitted": True}


@router.get("/manual-eval/export")
async def export(state: Any = Depends(get_state), run_id: str | None = None):
    rows = await state.repo.export_manual_scores(run_id=run_id)
    return {"items": rows, "count": len(rows)}
