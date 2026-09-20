"""文字用例库 REST（§15，T1）：用例 CRUD + 执行回填查询。

回填写入（T2 回填器）与版本报告（T3）后续接入；本路由只管文字用例库本身。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aite.service.case_docs import resolve_case_ref, update_case_doc
from aite.web.deps import get_state

router = APIRouter(tags=["testcases"])


@router.get("/test-cases")
async def list_test_cases(domain: str | None = None,
                          status: str | None = None,
                          priority: str | None = None, limit: int = 200,
                          state: Any = Depends(get_state)):
    items = await state.repo.list_case_docs(domain=domain, status=status,
                                            priority=priority, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/test-cases/{ref}")
async def get_test_case(ref: str, state: Any = Depends(get_state)):
    try:
        row = await resolve_case_ref(state.repo, ref)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    runs = await state.repo.list_case_runs(case_id=row["id"], limit=20)
    return {**row, "runs": runs}


@router.post("/test-cases")
async def create_test_case(body: dict, state: Any = Depends(get_state)):
    from aite.service.case_docs import create_case_doc

    try:
        return await create_case_doc(state.repo, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/test-cases/{ref}")
async def patch_test_case(ref: str, body: dict,
                          state: Any = Depends(get_state)):
    try:
        await resolve_case_ref(state.repo, ref)
        return await update_case_doc(state.repo, ref, body)
    except ValueError as exc:
        status = 404 if "未知用例" in str(exc) else 422
        raise HTTPException(status, str(exc)) from exc


@router.delete("/test-cases/{ref}")
async def delete_test_case(ref: str, state: Any = Depends(get_state)):
    try:
        row = await resolve_case_ref(state.repo, ref)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    deleted = await state.repo.delete_case_doc(row["id"])
    return {"deleted": deleted, "case_no": row["case_no"]}


@router.get("/test-cases/{ref}/runs")
async def list_test_case_runs(ref: str, version: str | None = None,
                              state: Any = Depends(get_state)):
    try:
        row = await resolve_case_ref(state.repo, ref)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    items = await state.repo.list_case_runs(case_id=row["id"], version=version)
    return {"items": items, "count": len(items)}
