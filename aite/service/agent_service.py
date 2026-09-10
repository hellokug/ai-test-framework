"""执行机纳管 service（模块 G）：派发/回传/stale 回收的业务编排。

形态：拉取式 agent（出站 HTTP）+ 结果 HTTP 回传 master 统一入库。
一期范围：压测域两引擎（evalscope-perf / locust）；协议与引擎域解耦，
二期放开其他域只需改白名单。全部函数 async（Web 直接 await；CLI 派发
经 asyncio.run 桥接，见 cli/main.py）。
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import fields as dataclass_fields
from datetime import datetime
from pathlib import Path
from typing import Any

from aite import config
from aite.tools.base import ToolMetricRow, ToolRunResult

# 一期压测域白名单（master 派发侧与 agent 执行侧双保险）
AGENT_ENGINES = ("evalscope-perf", "locust")
STALE_AFTER_SECONDS = 30.0   # 心跳超时判定


async def dispatch_task(repo: Any, raw: dict, node: str | None = None) -> dict:
    """校验并创建派发任务：工具域校验 + 引擎白名单 + 节点存在性（名称或 id）。"""
    from aite.service import resolve_suite

    sreq = resolve_suite(raw)
    if not sreq.is_tool:
        raise ValueError("agent dispatch 只支持工具域 suite（engine 字段）")
    engine = raw.get("engine")
    if engine not in AGENT_ENGINES:
        raise ValueError(f"agent dispatch 一期仅支持 {list(AGENT_ENGINES)}，"
                         f"收到 {engine!r}")
    node_id = None
    if node is not None:
        agents = await repo.list_agents()
        found = next((a for a in agents
                      if a["id"] == node or a["name"] == node), None)
        if found is None:
            raise ValueError(f"执行机不存在: {node}")
        node_id = found["id"]
    return await repo.create_agent_task(sreq.run_name, engine, raw, node_id)


async def sweep_stale(repo: Any) -> int:
    """懒式 stale 回收（路由在 agents 相关请求时顺带调用）。"""
    return await repo.sweep_stale_agents(STALE_AFTER_SECONDS)


async def receive_result(repo: Any, task: dict, payload: dict) -> dict:
    """回传结果入库：master 重建产出目录 → ToolRunResult 重建 → 统一入库。

    payload：{status: done|error, error?, rows, raw_score, failures,
              report_files, perf, timeseries?}
    返回 {run_id, status}。error 回传同样建 error run 行（保留审计，
    与本地执行失败路径一致）。
    """
    from aite.tools import parse_tool_suite, persist_tool_result

    status = payload.get("status", "error")
    work_dir = _agent_work_dir(task["run_name"])
    work_dir.mkdir(parents=True, exist_ok=True)
    result = _rebuild_result(payload, work_dir)
    timeseries = payload.get("timeseries")
    if timeseries is not None:
        (work_dir / "timeseries.json").write_text(
            json.dumps(timeseries, ensure_ascii=False), encoding="utf-8")

    if status == "done":
        run = await persist_tool_result(task["suite_raw"], repo, result, work_dir)
        await repo.update_agent_task(task["id"], status="done",
                                     result={"status": run.status,
                                             "run_id": run.run_id})
        return {"run_id": run.run_id, "status": run.status}

    await repo.update_agent_task(task["id"], status="error",
                                 error=payload.get("error"))
    req, cfg_snapshot = parse_tool_suite(task["suite_raw"])
    suite_id = await repo.upsert_suite(req.run_name, cfg_snapshot)
    run_id = await repo.create_run(req.run_name, cfg_snapshot, suite_id)
    from aite.core.types import utcnow

    await repo.update_run(run_id, status="error",
                          error=str(payload.get("error", "unknown"))[:2000],
                          finished_at=utcnow(),
                          details={"work_dir": str(work_dir)})
    return {"run_id": run_id, "status": "error"}


def serialize_result(result: ToolRunResult, work_dir: Path) -> dict:
    """agent 侧：ToolRunResult + timeseries 文件内容 → 可回传 payload。"""
    payload: dict[str, Any] = {
        "status": "done",
        "rows": [{k: getattr(r, k) for k in (f.name for f in dataclass_fields(r))}
                 for r in result.rows],
        "raw_score": result.raw_score,
        "failures": result.failures,
        "report_files": result.report_files,
        "perf": result.perf,
    }
    ts_path = work_dir / "timeseries.json"
    if ts_path.is_file():
        with contextlib.suppress(ValueError):
            payload["timeseries"] = json.loads(
                ts_path.read_text(encoding="utf-8"))   # 损坏时序不阻断回传
    return payload


# ---------------------------------------------------------------- internal

def _agent_work_dir(run_name: str) -> Path:
    """master 侧回传产物重建目录（timeseries 落盘，供大屏端点读取）。"""
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return Path(config.DEFAULT_REPORTS_DIR) / "agent" / run_name / stamp


def _rebuild_result(payload: dict, work_dir: Path) -> ToolRunResult:
    """回传 payload → ToolRunResult（report_files 指向 master 侧重建目录）。"""
    valid = {f.name for f in dataclass_fields(ToolMetricRow)}
    rows = [ToolMetricRow(**{k: v for k, v in row.items() if k in valid})
            for row in payload.get("rows", [])]
    report_files: dict[str, str] = {}
    ts_path = work_dir / "timeseries.json"
    if ts_path.is_file():
        report_files["timeseries.json"] = str(ts_path)
    return ToolRunResult(
        rows=rows,
        raw_score=payload.get("raw_score") or {},
        failures=payload.get("failures") or [],
        report_files=report_files,
        perf=payload.get("perf") or {},
    )
