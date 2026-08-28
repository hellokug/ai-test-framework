"""Model registry management (target / judge model configs)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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
    return {"id": model_id}


@router.delete("/models/{model_id}")
async def delete_model(model_id: str, state: Any = Depends(get_state)):
    ok = await state.repo.delete_model_config(model_id)
    if not ok:
        raise HTTPException(404, "model not found")
    return {"deleted": True}
