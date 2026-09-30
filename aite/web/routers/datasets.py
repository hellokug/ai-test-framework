"""数据集统一目录 REST（docs/domain-backends.md §12.3 的 D3 平台切片）。

类型校验与引用解析走 datasets/catalog 单一来源；功能型摄取经 service 层
（web_service.import_functional_dataset）过 harness.Case 真实校验器；目录
读写全在 repository（原则 1/6）。perf 型登记自动发生于 POST /perf/datasets，
精度型经 `aite dataset add-hf` 登记。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aite.datasets.catalog import check_type, resolve_dataset_ref
from aite.service.web_service import import_functional_dataset
from aite.web.deps import get_state

router = APIRouter(tags=["datasets"])


class FunctionalImportIn(BaseModel):
    name: str
    jsonl: str
    notes: str = ""
    force: bool = False


@router.get("/datasets")
async def list_datasets(state: Any = Depends(get_state), type: str | None = None,
                        limit: int = 200):
    try:
        type_ = check_type(type) if type else None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    items = await state.repo.list_dataset_catalog(type_=type_, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/datasets/{ref}")
async def get_dataset(ref: str, state: Any = Depends(get_state)):
    try:
        return await resolve_dataset_ref(state.repo, ref)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/datasets/{ref}")
async def delete_dataset(ref: str, state: Any = Depends(get_state)):
    """删除目录登记；数据文件保留（与 `aite dataset rm` 同语义）。"""
    try:
        row = await resolve_dataset_ref(state.repo, ref)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    deleted = await state.repo.delete_dataset_catalog(row["id"])
    return {"deleted": deleted, "type": row["type"], "name": row["name"]}


@router.post("/datasets/functional/import", status_code=201)
async def import_functional(body: FunctionalImportIn,
                            state: Any = Depends(get_state)):
    try:
        return await import_functional_dataset(
            state.repo, name=body.name, jsonl=body.jsonl,
            notes=body.notes, force=body.force)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
