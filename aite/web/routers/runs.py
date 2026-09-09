"""Runs: list / get / launch (bg task) / case results / metric results / SSE."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from aite.core.engine import ExecutionEngine
from aite.core.loader import load_suite
from aite.web.deps import get_state

router = APIRouter(tags=["runs"])


class LaunchRequest(BaseModel):
    # exactly one of: inline yaml text, config object, or existing suite name
    config_yaml: str | None = None
    config: dict[str, Any] | None = None
    suite_name: str | None = None
    limit: int | None = None


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/runs")
async def list_runs(state: Any = Depends(get_state), limit: int = 50, offset: int = 0,
                    run_name: str | None = None):
    runs = await state.repo.list_runs(limit=limit, offset=offset, run_name=run_name)
    return {"items": runs, "count": len(runs)}


@router.post("/runs")
async def launch_run(body: LaunchRequest, state: Any = Depends(get_state)):
    """Launch a run in the background; returns a task handle immediately."""
    try:
        if body.config_yaml:
            raw = yaml.safe_load(body.config_yaml)
        elif body.config:
            raw = dict(body.config)
        elif body.suite_name:
            stored = await state.repo.get_suite_by_name(body.suite_name)
            if not stored or not stored.get("config"):
                raise HTTPException(404, f"suite '{body.suite_name}' has no stored config")
            raw = stored["config"]
        else:
            raise HTTPException(422, "provide config_yaml, config or suite_name")
        if not isinstance(raw, dict):
            raise HTTPException(422, "suite config must be a mapping")
        if raw.get("engine"):
            # 工具域（evalscope/pytest/evalscope-perf/locust）：与 CLI 同源路由，
            # parse_tool_suite 做 fail-fast 校验（SuiteConfig extra=forbid 不认 engine）
            from aite.tools import parse_tool_suite

            parse_tool_suite(raw)
            suite = raw
        else:
            suite = load_suite(raw)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"invalid suite config: {exc}") from exc

    is_tool = isinstance(suite, dict)
    bg_id = await state.repo.create_bg_task(
        "run_suite", {"run_name": suite.run_name if not is_tool else suite.get("run_name"),
                      "limit": body.limit})
    task = asyncio.create_task(
        _execute_tool_run(state, bg_id, suite, body.limit) if is_tool
        else _execute_run(state, bg_id, suite, body.limit))
    state.tasks[bg_id] = task
    return {"task_id": bg_id, "run_name": suite.get("run_name") if is_tool else suite.run_name,
            "status": "started"}


async def _execute_tool_run(state: Any, bg_id: str, raw: dict, limit: int | None) -> None:
    """工具域后台执行：run_tool_suite（与 CLI aite run -c 同源；limit→limit_per_subset）。"""
    from aite.tools import run_tool_suite

    if limit:
        raw.setdefault("tools", {}).setdefault("limit_per_subset", limit)

    await state.repo.update_bg_task(bg_id, status="running")
    try:
        report = await run_tool_suite(raw, state.repo)
        await state.repo.update_bg_task(
            bg_id, status="done", run_id=report.run_id,
            result={"status": report.status, "summaries": len(report.summaries),
                    "reports": report.details.get("reports", {})})
        state.bus.publish(f"runs:{bg_id}", {"type": "done", "run_id": report.run_id,
                                            "status": report.status})
    except Exception as exc:  # noqa: BLE001
        await state.repo.update_bg_task(bg_id, status="error", error=str(exc)[:2000])
        state.bus.publish(f"runs:{bg_id}", {"type": "error", "error": str(exc)})
    finally:
        state.bus.close(f"runs:{bg_id}")
        state.tasks.pop(bg_id, None)


async def _execute_run(state: Any, bg_id: str, suite: Any, limit: int | None) -> None:
    def on_event(event: dict) -> None:
        state.bus.publish(f"runs:{bg_id}", event)

    await state.repo.update_bg_task(bg_id, status="running")
    engine = ExecutionEngine(state.repo, event_cb=on_event)
    try:
        report = await engine.run_suite(suite, limit=limit)
        await state.repo.update_bg_task(
            bg_id, status="done", run_id=report.run_id,
            result={"status": report.status, "summaries": len(report.summaries),
                    "reports": report.details.get("reports", {})})
        state.bus.publish(f"runs:{bg_id}", {"type": "done", "run_id": report.run_id,
                                            "status": report.status})
    except Exception as exc:  # noqa: BLE001
        await state.repo.update_bg_task(bg_id, status="error", error=str(exc)[:2000])
        state.bus.publish(f"runs:{bg_id}", {"type": "error", "error": str(exc)})
    finally:
        state.bus.close(f"runs:{bg_id}")
        state.tasks.pop(bg_id, None)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, state: Any = Depends(get_state)):
    run = await state.repo.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    run["summaries"] = await state.repo.list_metric_summaries(run_id)
    return run


@router.get("/runs/{run_id}/cases")
async def run_cases(run_id: str, state: Any = Depends(get_state),
                    limit: int | None = Query(None, ge=1, le=2000)):
    results = await state.repo.list_case_results(run_id, limit=limit)
    metrics = await state.repo.list_metric_results(run_id)
    by_case: dict[int, list[dict]] = {}
    for m in metrics:
        by_case.setdefault(m["case_result_id"], []).append(
            {k: m[k] for k in ("metric", "value", "passed", "error", "source")})
    for r in results:
        r["metrics"] = by_case.get(r["id"], [])
    return {"items": results, "count": len(results)}


@router.get("/runs/{run_id}/metrics")
async def run_metrics(run_id: str, state: Any = Depends(get_state),
                      metric: str | None = None):
    return {"items": await state.repo.list_metric_results(run_id, metric=metric)}


@router.get("/runs/{run_id}/timeseries")
async def run_timeseries(run_id: str, state: Any = Depends(get_state)):
    """压测时序（大屏曲线数据源）：读 run.details.work_dir 下 timeseries.json。

    形状 {engine, bucket_seconds, series: [{users, run, label, points}],
    errors: [{key, count}]}；points 每元素
    {t, ttft_avg, ttft_p95, latency_avg, latency_p95, rps, tok_s, errors}。
    非压测 run 或未产出 → 404。
    """
    run = await state.repo.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    work_dir = (run.get("details") or {}).get("work_dir")
    ts_path = Path(work_dir) / "timeseries.json" if work_dir else None
    if not ts_path or not ts_path.is_file():
        raise HTTPException(404, "no perf timeseries for this run")
    try:
        data = json.loads(ts_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise HTTPException(500, f"corrupted timeseries: {exc}") from exc
    data["run_id"] = run_id
    data["status"] = run["status"]
    return data


async def _stream(state: Any, request: Request, channel: str):
    async def gen():
        async for event in state.bus.subscribe(channel):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") in ("done", "error", "end"):
                break

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/tasks/{task_id}/stream")
async def stream_task(task_id: str, request: Request, state: Any = Depends(get_state)):
    return await _stream(state, request, f"runs:{task_id}")


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request, state: Any = Depends(get_state)):
    """Live progress by run_id：发布端 channel 一律 `runs:{task_id}`，此处先反查
    run 对应的后台任务再订阅；无后台任务（如 CLI 发起的历史 run）直接回一条
    终态事件关流——订阅不存在的频道会永久挂起。"""
    run = await state.repo.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    task = await state.repo.get_bg_task_by_run(run_id)
    if task:
        return await _stream(state, request, f"runs:{task['id']}")
    # 历史 run：无实时流，回存态终态事件（SSE 语义保持一致：一帧 + 关流）
    event = {"type": "done", "run_id": run_id, "status": run["status"]}

    async def gen():
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
