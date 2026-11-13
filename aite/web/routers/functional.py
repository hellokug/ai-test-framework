"""功能域评估场景 / 脚本（用例）/ 维度标签 REST（V1-PLAN §10.4/§10.5）。

路由只做「参数校验 → service（functional_assets）→ 返回」，磁盘与派生逻辑
全在 service 层（原则 1）；错误映射：FileNotFoundError→404、
FileExistsError→409、ValueError→422。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from aite.service import functional_assets as assets
from aite.service import version_results as vresults
from aite.web.deps import get_state

router = APIRouter(tags=["functional"])


@contextmanager
def _asset_errors():
    """资产层业务异常 → HTTP 语义（404/409/422），其余原样抛出。"""
    try:
        yield
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class ScenarioIn(BaseModel):
    id: str = Field(description="场景英文编号：小写字母开头，允许数字/-/_，2-64 位；"
                                "目录名/接口寻址用，创建后不可改")
    display_name: str = Field(default="", description="场景中文名称：卡片标题、"
                                                      "场景树、质量大屏维度行名；缺省回退 id")
    dimension: str = Field(default="", description="维度标签（选自共享标签库）")
    description: str = Field(default="", description="一句话说明该场景测什么")


class DimTagIn(BaseModel):
    name: str


class ScriptIn(BaseModel):
    scenario_id: str = Field(description="归属场景英文编号")
    script_id: str = Field(description="脚本英文编号（= jsonl 文件名），全平台唯一")
    name: str = Field(description="脚本中文名称（测试意图）")
    case_type: str = Field(default="基础", description="类型标签：基础/边界/异常/兼容/迁移")
    external_ref: str = Field(default="", description="外部用例平台编号（映射键）")
    rows: list[dict[str, Any]] = Field(description="数据行（harness.Case 同构）")


class ScriptPatch(BaseModel):
    name: str | None = None
    case_type: str | None = None
    external_ref: str | None = None
    rows: list[dict[str, Any]] | None = None


class ScriptImportIn(BaseModel):
    scenario_id: str = Field(description="目标场景英文编号")
    script_id: str = Field(description="脚本英文编号（= jsonl 文件名）")
    name: str = Field(description="脚本中文名称（测试意图）")
    case_type: str = Field(default="基础", description="类型标签：基础/边界/异常/兼容/迁移")
    external_ref: str = Field(default="", description="外部用例平台编号（映射键）")
    jsonl: str = Field(default="", description="粘贴的 jsonl 文本（与 dataset 二选一）")
    dataset: str = Field(default="", description="平台数据集名（与 jsonl 二选一）")


@router.get("/functional/scenarios")
async def list_scenarios():
    with _asset_errors():
        items = await asyncio.to_thread(assets.scan_scenarios)
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
async def delete_scenario(scenario_id: str, force: bool = False):
    """默认有脚本/用例拒删（409）；`?force=true` 确认后级联删除整目录。"""
    with _asset_errors():
        return await asyncio.to_thread(assets.delete_scenario, scenario_id, force)


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


@router.get("/functional/tools")
async def list_tools():
    """工具库（tool_calls 工具定义，OpenAI tools 条目）+ 被引用脚本。"""
    with _asset_errors():
        items = await asyncio.to_thread(assets.list_tools)
    return {"items": items, "count": len(items)}


@router.post("/functional/tools", status_code=201)
async def create_tool(body: dict[str, Any] = Body(...)):
    """body = OpenAI tools 条目：{"type": "function", "function": {name, description, parameters}}。"""
    with _asset_errors():
        return await asyncio.to_thread(assets.create_tool, body)


@router.put("/functional/tools/{name}")
async def update_tool(name: str, body: dict[str, Any] = Body(...)):
    """整体替换定义（不可改名）；引用该工具的场景自动重派生 cases.jsonl。"""
    with _asset_errors():
        return await asyncio.to_thread(assets.update_tool, name, body)


@router.delete("/functional/tools/{name}")
async def delete_tool(name: str):
    """仍被数据行引用 → 409（先移除引用）。"""
    with _asset_errors():
        return await asyncio.to_thread(assets.delete_tool, name)


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
