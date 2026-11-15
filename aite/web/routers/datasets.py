"""数据集统一目录 REST（docs/domain-backends.md §12.3 的 D3 平台切片）。

类型校验与引用解析走 datasets/catalog 单一来源；文件面（上传/下载/在线
导入/路径与在线源设置）在 service/dataset_files + datasets/settings。功能型
摄取经 service 层（web_service.import_functional_dataset）过 harness.Case
真实校验器；目录读写全在 repository（原则 1/6）。perf 型登记经
POST /perf/datasets（场景生成）或 /datasets/upload（真实负载导入），
精度型在线导入经 hf/remote import。

路由声明顺序注意：``/datasets/settings``、``/datasets/online/status`` 等
静态段必须排在 ``/datasets/{ref}`` 之前（FastAPI 按声明顺序匹配）。
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from aite.datasets import settings as ds_settings
from aite.datasets.catalog import check_type, resolve_dataset_ref
from aite.service import dataset_files
from aite.service.web_service import import_functional_dataset
from aite.web.deps import get_state

router = APIRouter(tags=["datasets"])


class FunctionalImportIn(BaseModel):
    name: str
    jsonl: str
    notes: str = ""
    force: bool = False


class DatasetUploadIn(BaseModel):
    type: str
    name: str
    filename: str = "dataset.jsonl"
    content: str          # 数据集文件文本（jsonl/json/csv 均为文本格式）
    notes: str = ""
    force: bool = False
    capabilities: list[str] | None = None  # 能力维度（accuracy/perf 适用）
    ownership: str | None = None  # 私有性 open/self；缺省按 source 推导（upload=自建）


class HfImportIn(BaseModel):
    hf_id: str
    name: str | None = None
    type: str = "accuracy"  # accuracy（题库）/ perf（开源语料池·负载）
    split: str = "test"
    subset: str | None = None
    n: int | None = None
    seed: int = 42
    fields: Any = None    # dict 或 "input=question,expected=answer" 形式
    notes: str = ""
    capabilities: list[str] | None = None  # 能力维度（基准库「选用」自动预填）
    ownership: str | None = None  # 私有性 open/self；缺省按 source 推导（hf=开源）


class RemoteImportIn(BaseModel):
    type: str
    name: str
    url: str = ""         # 缺省用设置里的 remote_url；结尾带 / 按 <地址>/<name>.jsonl 拼接
    notes: str = ""
    force: bool = False
    capabilities: list[str] | None = None  # 能力维度（accuracy/perf 适用）
    ownership: str | None = None  # 私有性 open/self；缺省按 source 推导（remote=自建）


@router.get("/datasets")
async def list_datasets(state: Any = Depends(get_state), type: str | None = None,
                        limit: int = 200):
    """本地数据集总览：catalog 登记 ⊕ 本机注册表/目录扫描（展示层合并）。

    未登记但本机已有的条目带 ``registered: false``（source=registry/local-file）；
    行带 ``origin``（local/online，本地导入 vs 在线拉取）；catalog 是写侧权威，
    删除/覆盖仍只作用于登记行。
    """
    try:
        type_ = check_type(type) if type else None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    items = await state.repo.list_dataset_catalog(type_=type_, limit=limit)
    items = dataset_files.merge_local_rows(items, only=type_)
    return {"items": items, "count": len(items)}


@router.get("/datasets/settings")
async def get_dataset_settings():
    """路径与在线源设置：原始值 + 解析值 + 环境变量锁定标记。"""
    return ds_settings.snapshot()


@router.put("/datasets/settings")
async def put_dataset_settings(body: dict):
    try:
        values = ds_settings.save_settings(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"saved": True, "path": str(ds_settings.settings_path()),
            "settings": values}


@router.get("/datasets/online/status")
async def dataset_online_status():
    """在线源连通性探测：HF 镜像仓 + 远程拉取地址（OBS 桶等）。"""
    return dataset_files.probe_online()


@router.get("/datasets/online/datasets")
async def dataset_online_datasets(state: Any = Depends(get_state)):
    """evalscope 原生基准全集 + 本机可用标记（前端在线数据集板块数据源）。

    每条基准带 ``local``（已下载：dataset_id 命中本地 hf id / 名称相等）与
    ``local_like``（归一名前缀近似）；清单为静态集合，进程内缓存。
    """
    try:
        online = dataset_files.online_benchmarks("evalscope")
    except (RuntimeError, OSError) as exc:
        raise HTTPException(
            502, f"在线数据集列举失败（evalscope 不可用）: {exc}") from exc
    items = dataset_files.match_local_benchmarks(
        dataset_files.merge_local_rows(await state.repo.list_dataset_catalog()),
        online["items"])
    return {"engine": online["engine"], "cached": online["cached"],
            "count": len(items),
            "local_ready": sum(1 for m in items if m.get("local")),
            "items": items}


@router.post("/datasets/upload", status_code=201)
async def upload_dataset(body: DatasetUploadIn, state: Any = Depends(get_state)):
    """上传数据集文件（jsonl/json/csv 文本）→ 校验 → 落位 → 登记。"""
    try:
        return await dataset_files.upload_dataset(
            state.repo, dataset_type=body.type, name=body.name,
            filename=body.filename, content=body.content, notes=body.notes,
            force=body.force, capabilities=body.capabilities,
            ownership=body.ownership)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/datasets/hf/import", status_code=201)
async def hf_import(body: HfImportIn, state: Any = Depends(get_state)):
    """HF 镜像仓导入（下载→字段映射→采样→落位→登记，之后完全离线可用）。"""
    try:
        return await dataset_files.hf_import(
            state.repo, hf_id=body.hf_id, name=body.name,
            dataset_type=body.type, split=body.split, subset=body.subset,
            n=body.n, seed=body.seed, fields=body.fields, notes=body.notes,
            capabilities=body.capabilities, ownership=body.ownership)
    except RuntimeError as exc:          # datasets 库未安装
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:            # 字段映射/记录形状问题
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:               # 网络/镜像不可达（requests 异常基类）
        raise HTTPException(502, f"HF 拉取失败（检查 hf_endpoint 连通性）: {exc}") from exc


@router.post("/datasets/remote/import", status_code=201)
async def remote_import(body: RemoteImportIn, state: Any = Depends(get_state)):
    """远程地址拉取（OBS 桶/其他直链）→ 复用上传校验管线 → 登记。"""
    try:
        return await dataset_files.remote_import(
            state.repo, dataset_type=body.type, name=body.name, url=body.url,
            notes=body.notes, force=body.force,
            capabilities=body.capabilities, ownership=body.ownership)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"远程拉取失败: {exc}") from exc


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


@router.get("/datasets/{ref}")
async def get_dataset(ref: str, state: Any = Depends(get_state)):
    try:
        return await resolve_dataset_ref(state.repo, ref)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/datasets/{ref}/download")
async def download_dataset(ref: str, state: Any = Depends(get_state)):
    """下载数据集文件（登记本地文件优先，包内置精度样本回退）。"""
    try:
        out = await dataset_files.resolve_download(state.repo, ref)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    if out["kind"] == "file":
        return FileResponse(out["path"], filename=out["filename"],
                            media_type="application/octet-stream")
    return Response(content=out["content"],
                    media_type="application/octet-stream",
                    headers={"Content-Disposition":
                             f'attachment; filename="{out["filename"]}"'})


@router.delete("/datasets/{ref}")
async def delete_dataset(ref: str, state: Any = Depends(get_state)):
    """删除目录登记；数据文件保留（与 `aite dataset rm` 同语义）。"""
    try:
        row = await resolve_dataset_ref(state.repo, ref)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    deleted = await state.repo.delete_dataset_catalog(row["id"])
    return {"deleted": deleted, "type": row["type"], "name": row["name"]}
