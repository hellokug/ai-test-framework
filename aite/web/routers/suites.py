"""Suites CRUD (stored suite configs reusable for runs)."""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aite.service import resolve_suite
from aite.web.deps import get_state

router = APIRouter(tags=["suites"])


class SuiteIn(BaseModel):
    name: str
    description: str = ""
    config_yaml: str | None = None   # full suite yaml (validated)
    config: dict[str, Any] | None = None


@router.get("/suites")
async def list_suites(state: Any = Depends(get_state)):
    items = await state.repo.list_suites()
    return {"items": items, "count": len(items)}


@router.get("/suites/{name}")
async def get_suite(name: str, state: Any = Depends(get_state)):
    suite = await state.repo.get_suite_by_name(name)
    if not suite:
        raise HTTPException(404, "suite not found")
    return suite


@router.post("/suites")
async def save_suite(body: SuiteIn, state: Any = Depends(get_state)):
    try:
        if body.config_yaml:
            parsed = yaml.safe_load(body.config_yaml)
        elif body.config:
            parsed = dict(body.config)
        else:
            raise HTTPException(422, "config_yaml or config required")
        parsed.setdefault("run_name", body.name)
        # engine 路由 + 校验收口 service 层（CLI/POST /runs 同源）
        sreq = resolve_suite(parsed)
        # 工具域存原 dict（自研域存强类型 snapshot）
        snapshot = parsed if sreq.is_tool else sreq.config.snapshot()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"invalid suite config: {exc}") from exc

    suite_id = await state.repo.upsert_suite(body.name, snapshot)
    return {"id": suite_id, "name": body.name, "validated": True}


@router.delete("/suites/{name}")
async def delete_suite(name: str, state: Any = Depends(get_state)):
    deleted = await state.repo.delete_suite(name)
    if not deleted:
        raise HTTPException(404, "suite not found")
    return {"deleted": name}
