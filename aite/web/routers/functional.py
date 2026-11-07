"""功能域评估场景 / 脚本（用例）/ 维度标签 REST（V1-PLAN §10.4/§10.5）。

路由只做「参数校验 → service（functional_assets）→ 返回」，磁盘与派生逻辑
全在 service 层（原则 1）；错误映射：FileNotFoundError→404、
FileExistsError/ScenarioReadOnlyError→409、ValueError→422。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aite.service import functional_assets as assets
from aite.service import version_results as vresults
from aite.service.functional_assets import ScenarioReadOnlyError
from aite.web.deps import get_state

router = APIRouter(tags=["functional"])


@contextmanager
def _asset_errors():
    """资产层业务异常 → HTTP 语义（404/409/422），其余原样抛出。"""
    try:
        yield
    except ScenarioReadOnlyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class ScenarioIn(BaseModel):
    id: str
    display_name: str = ""
    dimension: str = ""
    description: str = ""


class DimTagIn(BaseModel):
    name: str


class ScriptIn(BaseModel):
    scenario_id: str
    script_id: str
    name: str
    case_type: str = "基础"
    external_ref: str = ""
    rows: list[dict[str, Any]]


class ScriptPatch(BaseModel):
    name: str | None = None
    case_type: str | None = None
    external_ref: str | None = None
    rows: list[dict[str, Any]] | None = None


class ScriptImportIn(BaseModel):
    scenario_id: str
    script_id: str
    name: str
    case_type: str = "基础"
    external_ref: str = ""
    jsonl: str = ""
    dataset: str = ""


@router.get("/functional/scenarios")
async def list_scenarios(source: str | None = None):
    with _asset_errors():
        items = await asyncio.to_thread(assets.scan_scenarios, source)
    return {"items": items, "count": len(items)}


@router.post("/functional/scenarios", status_code=201)
async def create_scenario(body: ScenarioIn, state: Any = Depends(get_state)):
    with _asset_errors():
        return await assets.create_scenario(state.repo, body.id, body.display_name,
                                            body.dimension, body.description)


@router.get("/functional/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str):
    with _asset_errors():
        return await asyncio.to_thread(assets.get_scenario, scenario_id)


@router.delete("/functional/scenarios/{scenario_id}")
async def delete_scenario(scenario_id: str):
    with _asset_errors():
        return await asyncio.to_thread(assets.delete_scenario, scenario_id)


@router.get("/functional/scripts")
async def list_scripts(scenario: str | None = None):
    with _asset_errors():
        items = await asyncio.to_thread(assets.list_scripts, scenario)
    return {"items": items, "count": len(items)}


@router.post("/functional/scripts", status_code=201)
async def create_script(body: ScriptIn):
    with _asset_errors():
        return await asyncio.to_thread(assets.create_script, **body.model_dump())


@router.get("/functional/scripts/{script_id}")
async def get_script(script_id: str):
    with _asset_errors():
        return await asyncio.to_thread(assets.get_script, script_id)


@router.patch("/functional/scripts/{script_id}")
async def update_script(script_id: str, body: ScriptPatch):
    with _asset_errors():
        return await asyncio.to_thread(assets.update_script, script_id,
                                       **body.model_dump())


@router.delete("/functional/scripts/{script_id}")
async def delete_script(script_id: str):
    with _asset_errors():
        return await asyncio.to_thread(assets.delete_script, script_id)


@router.post("/functional/scripts/import", status_code=201)
async def import_script(body: ScriptImportIn, state: Any = Depends(get_state)):
    with _asset_errors():
        return await assets.import_script(state.repo, **body.model_dump())


@router.get("/functional/dim-tags")
async def list_dim_tags(state: Any = Depends(get_state)):
    with _asset_errors():
        items = await assets.list_dim_tags(state.repo)
    return {"items": items, "count": len(items)}


@router.post("/functional/dim-tags", status_code=201)
async def create_dim_tag(body: DimTagIn, state: Any = Depends(get_state)):
    with _asset_errors():
        return await assets.create_dim_tag(state.repo, body.name)


@router.get("/functional/version-results")
async def list_version_results(version: str | None = None,
                               model: str | None = None,
                               state: Any = Depends(get_state)):
    items = await state.repo.list_version_results(
        domain="functional", version=version, model=model)
    return {"items": items, "count": len(items)}


@router.get("/functional/quality/board")
async def quality_board(model: str, target: str, baseline: str,
                        state: Any = Depends(get_state)):
    with _asset_errors():
        return await vresults.board(state.repo, model=model, target=target,
                                    baseline=baseline)
