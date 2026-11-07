"""精度域脚本 REST（V1-PLAN §10.4 accuracy 形态）。

路由只做「参数校验 → service（accuracy_assets）→ 返回」；错误映射与
functional 路由同口径：FileNotFoundError→404、FileExistsError/
PermissionError（builtin 只读）→409、ValueError→422。
预置脚本 = examples/accuracy/*.yaml（随框架发布，只读可复制）；自定义 =
data/accuracy/scripts/<id>.yaml（spec 真实校验后落盘）。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aite.service import accuracy_assets as assets
from aite.web.deps import get_state  # noqa: F401 - 与其他路由同构（鉴权依赖 app 级）

router = APIRouter(tags=["accuracy"])


@contextmanager
def _asset_errors():
    """资产层业务异常 → HTTP 语义（404/409/422），其余原样抛出。"""
    try:
        yield
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class ScriptIn(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    spec: dict[str, Any]


class ScriptPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    spec: dict[str, Any] | None = None


@router.get("/accuracy/scripts")
async def list_scripts(source: str | None = None):
    with _asset_errors():
        return assets.list_scripts(source=source)


@router.get("/accuracy/scripts/{sid}")
async def get_script(sid: str):
    with _asset_errors():
        return assets.get_script(sid)


@router.post("/accuracy/scripts", status_code=201)
async def save_script(body: ScriptIn):
    with _asset_errors():
        return assets.save_script(body.id, body.name.strip(),
                                  body.description, body.spec)


@router.patch("/accuracy/scripts/{sid}")
async def update_script(sid: str, body: ScriptPatch):
    with _asset_errors():
        return assets.update_script(sid, name=body.name,
                                    description=body.description,
                                    spec=body.spec)


@router.delete("/accuracy/scripts/{sid}")
async def delete_script(sid: str):
    with _asset_errors():
        assets.delete_script(sid)
    return {"id": sid, "deleted": True}
