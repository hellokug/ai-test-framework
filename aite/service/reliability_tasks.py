"""可靠性 chaos 域任务装配（domain-backends.md）：故障注入 + 背景推理观测。

两种任务形态（同一执行器按 spec 分派）：
- **纯观测**：spec 与 POST /api/v1/chaos/observe 的 body 同构——
  {target, faults, interval?, duration?, prompt?, post_window?}；
  注入 → 探针观测 → 恢复 → 补测窗口 → RTO。
- **复合任务**（§12.4，D2）：多一个 `background` 键——性能数据集/负载参数铺
  背景流量，**注入窗 = 背景流量时长**，探针同窗观测；恢复后补测，RTO 判定。
  背景流量即 perf 套件（engine: locust/evalscope-perf），结果落公共 run 存储，
  概要并入任务 result.background。

结果内联任务表 result（时间线 + RTO + 背景概要，量小不另建明细表）。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from typing import Any

from aite.chaos.controller import run_with_chaos
from aite.chaos.model import validate_specs
from aite.chaos.observer import (
    ProbeConfig,
    compute_rto,
    observe_under_chaos,
    run_observation,
    summarize_probes,
)
from aite.core.taskkit import DomainTaskKit, EventSink, TaskExecutor, TaskRunContext
from aite.datasets.catalog import resolve_dataset_ref
from aite.service.suite_task import suite_summary
from aite.storage.models import ReliabilityTaskRow
from aite.web.events import EventBus

_MAX_OBSERVE_DURATION = 600.0      # 纯观测时长上限（秒），与同步 REST 端点同口径
_MAX_BACKGROUND_DURATION = 3600.0  # 复合任务背景流量时长上限（秒）


def build_observe_cfg(body: dict) -> tuple[list, ProbeConfig]:
    """spec body → (FaultSpec 列表, ProbeConfig)；校验失败抛 ValueError。"""
    target = body.get("target") or {}
    base_url = str(target.get("base_url") or "").strip().rstrip("/")
    model = str(target.get("model") or "").strip()
    if not base_url or not model:
        raise ValueError("target.base_url 与 target.model 必填")
    url = base_url if base_url.endswith("/chat/completions") \
        else base_url + "/chat/completions"
    specs = validate_specs(body.get("faults") or [])
    interval = float(body.get("interval") or 5.0)
    duration = float(body.get("duration") or 60.0)
    if interval <= 0 or duration <= 0 or duration > _MAX_OBSERVE_DURATION:
        raise ValueError(f"interval 须 >0；duration 须在 (0, "
                         f"{_MAX_OBSERVE_DURATION:.0f}] 秒内")
    api_key = os.environ.get(target.get("api_key_env") or "OPENAI_API_KEY", "EMPTY")
    cfg = ProbeConfig(url=url, model=model, api_key=api_key,
                      interval=interval, duration=duration,
                      prompt=str(body.get("prompt") or "ping"))
    return specs, cfg


async def build_background_suite(bg: dict, body: dict, repo: Any) -> dict:
    """background 段 → perf 套件 raw（target 继承顶层；dataset_ref 解析为路径）。"""
    if not isinstance(bg, dict) or not bg:
        raise ValueError("background 须为对象（engine/dataset_ref/run_name/perf）")
    engine = str(bg.get("engine") or "locust")
    if engine not in ("locust", "evalscope-perf"):
        raise ValueError(
            f"background.engine 仅支持 locust/evalscope-perf，当前 {engine!r}")
    perf = dict(bg.get("perf") or {})
    duration = float(perf.get("duration") or 0)
    if duration <= 0:
        raise ValueError("background.perf.duration 必配（注入窗 = 背景流量时长）")
    if duration > _MAX_BACKGROUND_DURATION:
        raise ValueError(
            f"background.perf.duration 上限 {_MAX_BACKGROUND_DURATION:.0f} 秒")
    if bg.get("dataset_ref"):
        row = await resolve_dataset_ref(repo, str(bg["dataset_ref"]))
        if row["type"] != "perf":
            raise ValueError("background.dataset_ref 须引用 perf 型数据集，"
                             f"当前 {row['type']!r}")
        perf["dataset"] = row["path"]
    if not perf.get("dataset") and not perf.get("prompt"):
        raise ValueError("background.perf 须配 dataset/dataset_ref 或 prompt 之一")
    perf.setdefault("mode", "baseline")
    perf.setdefault("stream", True)
    target = dict(body.get("target") or {})
    target.setdefault("type", "openai_compatible")
    target["stream"] = bool(perf.get("stream"))
    run_name = str(bg.get("run_name")
                   or f"reliability-bg-{time.strftime('%Y%m%d-%H%M%S')}")
    return {"engine": engine, "run_name": run_name,
            "target": target, "perf": perf}


class ObserveTaskExecutor(TaskExecutor):
    """观测/复合执行器：无 background 键 = 纯观测；有 = 复合任务
    （背景流量铺满注入窗 + 探针同窗观测 → 恢复 → 补测 → RTO）。"""

    async def execute(self, ctx: TaskRunContext) -> dict:
        if ctx.spec.get("background"):
            return await run_composite(ctx.spec, ctx)
        return await run_observe(ctx.spec)


async def run_observe(body: dict) -> dict:
    """纯观测：body → 校验 → 观测编排 → 附 probe 概要（REST 与任务共用）。"""
    specs, cfg = build_observe_cfg(body)
    result = await observe_under_chaos(
        specs, cfg, post_window=body.get("post_window"))
    result["probe"] = {"url": cfg.url, "model": cfg.model,
                       "interval": cfg.interval, "duration": cfg.duration}
    return result


async def run_composite(body: dict, ctx: TaskRunContext) -> dict:
    """复合任务：注入 → 背景流量+探针并发跑满注入窗 → 恢复 → 补测 → RTO。

    编排语义（run_with_chaos：注入全部并验证 → 业务 → finally 逆序恢复）：
    业务槽 = 背景流量（perf 套件，落公共 run 存储）与探针观测并发；探针窗口
    自动对齐背景流量时长（注入窗 = 背景时长），恢复后补测窗口捕捉首个 pass。
    """
    from aite.tools import run_tool_suite

    specs, cfg = build_observe_cfg(body)
    bg_raw = await build_background_suite(body.get("background") or {}, body,
                                          ctx.repo)
    bg_duration = float((bg_raw.get("perf") or {}).get("duration"))
    probe_cfg = replace(cfg, duration=bg_duration)

    async def business():
        load = asyncio.create_task(run_tool_suite(bg_raw, ctx.repo))
        probes = await run_observation(probe_cfg)
        report = await load          # 背景流量收尾后注入才恢复（防泄漏语义）
        return probes, report

    (probes, report), events = await run_with_chaos(specs, business)
    post_window = body.get("post_window")
    post_window = cfg.interval * 3 if post_window is None else float(post_window)
    post = await run_observation(replace(cfg, duration=post_window))
    all_probes = probes + post
    return {
        "events": events,
        "probes": all_probes,
        "rto": compute_rto(all_probes, events),
        "summary": summarize_probes(all_probes),
        "probe": {"url": cfg.url, "model": cfg.model, "interval": cfg.interval,
                  "duration": bg_duration, "background": True},
        "background": suite_summary(report),
    }


def build_reliability_task_kit(bus: EventSink | None = None) -> DomainTaskKit:
    return DomainTaskKit("reliability", ReliabilityTaskRow, ObserveTaskExecutor(),
                         bus or EventBus())
