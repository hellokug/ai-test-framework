"""全局 common 预制只读端点（模型预制 target.profile 的真实来源 = data/common.yaml）
+ 全局资源管理（密钥库 / 版本登记，V1-PLAN §10.6）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aite.core.profiles import load_common
from aite.utils import secrets as secret_store
from aite.web.deps import get_state

router = APIRouter(tags=["common"])


class SecretIn(BaseModel):
    value: str


class VersionIn(BaseModel):
    model: str
    version: str
    description: str = ""


@router.get("/versions")
async def list_versions(model: str | None = None,
                        state: Any = Depends(get_state)) -> dict[str, Any]:
    """全局版本登记清单（被测系统/模型 × 版本；?model= 按模型过滤）。"""
    items = await state.repo.list_model_versions(model=model)
    return {"items": items, "count": len(items)}


@router.post("/versions", status_code=201)
async def create_version(body: VersionIn,
                         state: Any = Depends(get_state)) -> dict[str, Any]:
    if not body.model.strip() or not body.version.strip():
        raise HTTPException(422, "model 与 version 均必填")
    try:
        return await state.repo.create_model_version(
            {"model": body.model.strip(), "version": body.version.strip(),
             "description": body.description})
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/versions/{version_id}")
async def delete_version(version_id: str,
                         state: Any = Depends(get_state)) -> dict[str, bool]:
    if not await state.repo.delete_model_version(version_id):
        raise HTTPException(404, "version not found")
    return {"deleted": True}


@router.get("/secrets")
async def list_secrets() -> dict[str, Any]:
    """密钥清单（仅名称 + 掩码，明文/密文永不回传）。"""
    items = secret_store.list_secrets()
    return {"items": items, "count": len(items)}


@router.put("/secrets/{name}", status_code=200)
async def upsert_secret(name: str, body: SecretIn) -> dict[str, str]:
    try:
        mask = secret_store.set_secret(name, body.value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"name": name, "mask": mask}


@router.delete("/secrets/{name}")
async def delete_secret(name: str) -> dict[str, bool]:
    return {"deleted": secret_store.delete_secret(name)}


@router.get("/common/models")
async def list_common_models() -> dict[str, Any]:
    """模型预制清单：suite `target.profile` 可引用的名字 + 展示信息。"""
    models = load_common().get("models") or {}
    items = []
    for name, entry in models.items():
        info = entry if isinstance(entry, dict) else {"profile": entry}
        items.append({"name": name,
                      "model": info.get("model", ""),
                      "base_url": info.get("base_url", ""),
                      "api_key_env": info.get("api_key_env", "")})
    return {"items": items, "count": len(items)}
