"""Model registry management (target / judge model configs)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aite.core.profiles import remove_model_profile, upsert_model_profile
from aite.utils.secrets import resolve_secret
from aite.web.deps import get_state

router = APIRouter(tags=["models"])


class ModelIn(BaseModel):
    name: str
    type: str = "openai_compatible"
    base_url: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    params: dict[str, Any] = {}
    notes: str | None = None


@router.get("/models")
async def list_models(state: Any = Depends(get_state)):
    items = await state.repo.list_model_configs()
    return {"items": items, "count": len(items)}


@router.post("/models")
async def save_model(body: ModelIn, state: Any = Depends(get_state)):
    model_id = await state.repo.save_model_config(body.model_dump())
    # 同步 common.yaml 模型预制：注册即可被任务发起 target.profile 直接引用
    upsert_model_profile(body.name, {
        "model": body.model, "base_url": body.base_url,
        "api_key_env": body.api_key_env, "params": body.params or None,
    })
    # 注册即告知密钥可解析性（环境变量 > 密钥库）：未归档不阻断（密钥可后补录），前端据此提示
    key_archived = not body.api_key_env or resolve_secret(body.api_key_env) is not None
    return {"id": model_id, "common_profile": body.name, "key_archived": key_archived}


@router.delete("/models/{model_id}")
async def delete_model(model_id: str, state: Any = Depends(get_state)):
    name = next((m["name"] for m in await state.repo.list_model_configs()
                 if m["id"] == model_id), None)
    ok = await state.repo.delete_model_config(model_id)
    if not ok:
        raise HTTPException(404, "model not found")
    if name:
        remove_model_profile(name)   # 预制同步删除（手工维护的同名条目一并移除）
    return {"deleted": True}
