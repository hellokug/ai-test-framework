"""Runs: list / get / launch (bg task) / case results / metric results / SSE."""

from __future__ import annotations

import asyncio
import json
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
            suite = load_suite(yaml.safe_load(body.config_yaml))
        elif body.config:
            suite = load_suite(dict(body.config))
        elif body.suite_name:
            stored = await state.repo.get_suite_by_name(body.suite_name)
            if not stored or not stored.get("config"):
                raise HTTPException(404, f"suite '{body.suite_name}' has no stored config")
            suite = load_suite(stored["config"])
        else:
            raise HTTPException(422, "provide config_yaml, config or suite_name")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"invalid suite config: {exc}") from exc

    bg_id = await state.repo.create_bg_task(
        "run_suite", {"run_name": suite.run_name, "limit": body.limit})
    task = asyncio.create_task(_execute_run(state, bg_id, suite, body.limit))
    state.tasks[bg_id] = task
    return {"task_id": bg_id, "run_name": suite.run_name, "status": "started"}


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
    """Live progress for runs launched in this process (channel keyed by task id);
    historical runs expose their stored state instead."""
    run = await state.repo.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return await _stream(state, request, f"runs:{run_id}")
