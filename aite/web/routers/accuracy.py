"""精度域评估场景 / 脚本 REST（V1-PLAN §10.4 accuracy 形态）。

路由只做「参数校验 → service（accuracy_assets）→ 返回」；磁盘、派生与条数
口径全在 service 层（原则 1）；错误映射与 functional 路由同口径：
FileNotFoundError→404、FileExistsError（含场景有脚本拒删）→409、
ValueError→422。场景与脚本全部用户自管，无预置资产。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from aite.service import accuracy_assets as assets
from aite.web.deps import get_state

router = APIRouter(tags=["accuracy"])


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
    display_name: str = Field(default="", description="场景中文名称；缺省回退 id")
    dimension: str = Field(default="", description="维度标签（选自共享标签库）")
    description: str = Field(default="", description="一句话说明该场景测什么")


class ScriptIn(BaseModel):
    scenario_id: str = Field(description="归属场景英文编号")
    id: str = Field(description="脚本英文编号（= yaml 文件名），全平台唯一")
    name: str = Field(default="", description="脚本中文名称（缺省回退 id）")
    description: str = Field(default="", description="一句话测试意图（仅详情展示，清单不带）")
    spec: dict[str, Any] = Field(description="suite raw 同构任务 spec（保存经 resolve_suite 真实校验）")


class ScriptPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    spec: dict[str, Any] | None = None


@router.get("/accuracy/scenarios")
async def list_scenarios(state: Any = Depends(get_state)):
    with _asset_errors():
        items = await assets.scan_scenarios(state.repo)
    return {"items": items, "count": len(items)}


@router.post("/accuracy/scenarios", status_code=201)
async def create_scenario(body: ScenarioIn, state: Any = Depends(get_state)):
    with _asset_errors():
        return await assets.create_scenario(state.repo, body.id, body.display_name,
                                            body.dimension, body.description)


@router.get("/accuracy/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str, state: Any = Depends(get_state)):
    with _asset_errors():
        return await assets.get_scenario(state.repo, scenario_id)


@router.delete("/accuracy/scenarios/{scenario_id}")
async def delete_scenario(scenario_id: str, force: bool = False):
    """默认有脚本拒删（409）；`?force=true` 确认后级联删除整目录。"""
    with _asset_errors():
        return await assets.delete_scenario(scenario_id, force)


@router.get("/accuracy/scripts")
async def list_scripts(scenario: str | None = None,
                       state: Any = Depends(get_state)):
    """清单瘦身口径：数据集 + 条数 + 更新时间，无描述/spec（详情才有）。"""
    with _asset_errors():
        items = await assets.list_scripts(state.repo, scenario)
    return {"items": items, "count": len(items)}


@router.post("/accuracy/scripts", status_code=201)
async def create_script(body: ScriptIn, state: Any = Depends(get_state)):
    with _asset_errors():
        return await assets.create_script(
            state.repo, scenario_id=body.scenario_id, script_id=body.id,
            name=body.name.strip(), description=body.description, spec=body.spec)


@router.get("/accuracy/scripts/{script_id}")
async def get_script(script_id: str, state: Any = Depends(get_state)):
    with _asset_errors():
        return await assets.get_script(state.repo, script_id)


@router.patch("/accuracy/scripts/{script_id}")
async def update_script(script_id: str, body: ScriptPatch,
                        state: Any = Depends(get_state)):
    with _asset_errors():
        return await assets.update_script(state.repo, script_id,
                                          **body.model_dump())


@router.delete("/accuracy/scripts/{script_id}")
async def delete_script(script_id: str):
    with _asset_errors():
        return await assets.delete_script(script_id)
