"""性能场景数据集 REST（模块 J）：语料池 → 压测专用负载，前端对接入口。

生成产物为 jsonl（input 字段 + 长度/前缀元数据），suite yaml 经
``perf.dataset: <path>`` 直接被两引擎消费；产物目录随数据集设置落位
（``<datasets_dir>/datasets/perf``，缺省即 ``./data/datasets/perf``）。
路由只做「校验 → 调生成器/web_service → 返回」（原则 1），磁盘枚举在
web_service.list_perf_datasets。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aite.datasets import userstore
from aite.datasets.catalog import register_entry
from aite.service import web_service
from aite.tools.perf_dataset import generate_perf_dataset, validate_scenario
from aite.web.deps import get_state

router = APIRouter(tags=["perfdata"])


@router.post("/perf/datasets")
async def create_perf_dataset(body: dict, state: Any = Depends(get_state)):
    """生成压测场景数据集（语料池 + 长度分桶 + 前缀共享率，确定性可复现）。

    body：{corpus, count, input_lengths, input_weights?, prefix_sharing?,
    prefix_groups?, output_tokens?, seed?, name?}；产物落
    ``perf_datasets/<name>-<时间戳>.jsonl``（+ .stats.json），并登记
    dataset catalog（perf 型，name=场景名指向最新产物，seed=version）。
    """
    try:
        result = generate_perf_dataset(body, dest_dir=userstore.dataset_dir("perf"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await register_entry(
        state.repo, dataset_type="perf", name=str(body.get("name") or "perf-scene"),
        source="generated", path=str(result.get("path") or result.get("file") or ""),
        version=f"seed{body.get('seed', 42)}", stats=result.get("stats") or {},
        notes="perf 场景数据集（生成产物，同名单场景名指向最新产物）")
    return result


@router.get("/perf/datasets")
async def list_perf_datasets():
    """已生成的场景数据集清单（按时间倒序，含 stats 摘要）。"""
    items = web_service.list_perf_datasets()
    return {"items": items, "count": len(items)}


@router.post("/perf/datasets/validate")
async def validate_perf_dataset(body: dict):
    """场景配置干跑校验（不生成、不读语料池——前端表单实时反馈用）。"""
    try:
        spec = validate_scenario(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"valid": True, "spec": spec}
