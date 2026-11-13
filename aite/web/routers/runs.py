"""Runs: list / get / launch (bg task) / case results / metric results / SSE."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from aite import config
from aite.service import execute_suite, resolve_suite
from aite.service.web_service import filter_runs_by_domain, load_run_samples, load_run_timeseries
from aite.web.deps import get_state
from aite.web.sse import stream_events

router = APIRouter(tags=["runs"])


class LaunchRequest(BaseModel):
    # exactly one of: inline yaml text, config object, or existing suite name
    config_yaml: str | None = None
    config: dict[str, Any] | None = None
    suite_name: str | None = None
    limit: int | None = None
    chaos: list[dict[str, Any]] | None = None   # 故障注入声明（模块 I，包裹本次执行）
    resume: str | None = None   # 工具域续推档位 auto/true/false（None = yaml 值/缺省 auto）


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/runs")
async def list_runs(state: Any = Depends(get_state), limit: int = 50, offset: int = 0,
                    run_name: str | None = None, domain: str | None = None):
    """domain 过滤（V1 规划 §10.3）：按新→旧扫描 config.MAX_DOMAIN_SCAN 条，
    service 层内存过滤后再分页（原则 1：业务过滤不进 repo/SQL）。"""
    try:
        if domain:
            runs = filter_runs_by_domain(
                await state.repo.list_runs(limit=config.MAX_DOMAIN_SCAN,
                                           run_name=run_name), domain)
            total = len(runs)
            return {"items": runs[offset:offset + limit], "count": total}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
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
        sreq = resolve_suite(raw)   # engine 路由 + 校验（service 层，CLI 同源）
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"invalid suite config: {exc}") from exc

    bg_id = await state.repo.create_bg_task(
        "run_suite", {"run_name": sreq.run_name, "limit": body.limit,
                      "chaos": bool(body.chaos), "resume": body.resume})
    task = asyncio.create_task(
        _execute_run(state, bg_id, sreq, body.limit, chaos_raw=body.chaos,
                     resume=body.resume))
    state.tasks[bg_id] = task
    return {"task_id": bg_id, "run_name": sreq.run_name, "status": "started"}


async def _execute_run(state: Any, bg_id: str, sreq: Any, limit: int | None,
                       chaos_raw: list[dict] | None = None,
                       resume: str | None = None) -> None:
    """后台执行（四域统一走 service 的 execute_suite）。"""

    def on_event(event: dict) -> None:
        state.bus.publish(f"runs:{bg_id}", event)

    await state.repo.update_bg_task(bg_id, status="running")
    try:
        report = await execute_suite(sreq.raw, state.repo, limit=limit,
                                     event_cb=on_event, chaos_raw=chaos_raw,
                                     resume=resume)
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
    combined, errors: [{key, count}], summaries: [{metric, dataset, value,
    count, threshold, passed, higher_is_better}]}；points 每元素 {t, ts, users,
    ttft_avg, ttft_p95, latency_avg, latency_p95, tpot_avg, tpot_p95, itl_avg,
    itl_p95, rps, tok_s, errors}（t = 档内相对秒，ts = 墙钟 epoch 秒）；combined
    为各档按 ts 排序的全场阶梯曲线（t 重定基全场首秒）；summaries 为统计表
    数据源（overall 行 dataset=None 与 CLI 汇总同源 + 档级行）。非压测 run
    或未产出 → 404。
    """
    run = await state.repo.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    try:
        data = load_run_timeseries(run)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    if data is None:
        raise HTTPException(404, "no perf timeseries for this run")
    data["run_id"] = run_id
    data["status"] = run["status"]
    return data


@router.get("/runs/{run_id}/samples")
async def run_samples(run_id: str, state: Any = Depends(get_state),
                      limit: int = Query(200, ge=1, le=1000),
                      offset: int = Query(0, ge=0),
                      wrong_only: bool = False):
    """逐样本明细（精度诊断数据源）：读 run.details.work_dir 下
    reviews/**/*.jsonl（evalscope 逐样本评分契约），行含 score/passed、
    模型提取答案 vs 标准答案、预测原文与判定说明。工具域跑分 run 的
    test_case_result 恒为空（引擎不逐例驱动），逐样本真值只在此处。
    无 work_dir 或无逐样本产物（自研域/旧运行）→ 404。"""
    run = await state.repo.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    data = load_run_samples(run, limit=limit, offset=offset,
                            wrong_only=wrong_only)
    if data is None:
        raise HTTPException(404, "no per-sample data for this run")
    data["run_id"] = run_id
    data["status"] = run["status"]
    return data


async def _stream(state: Any, request: Request, channel: str):
    return stream_events(state.bus, request, channel)


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
