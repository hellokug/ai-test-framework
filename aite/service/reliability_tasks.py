"""可靠性 chaos 域任务装配（domain-backends.md）：故障注入 + 背景推理观测。

spec 与 POST /api/v1/chaos/observe 的 body 同构：
{target: {base_url, model, api_key_env?}, faults: [FaultSpec...],
 interval?, duration?, prompt?, post_window?}
观测编排走 observe_under_chaos（复用 run_with_chaos），结果内联任务表
result（时间线 + RTO，量小不另建明细表）。
"""

from __future__ import annotations

import os

from aite.chaos.model import validate_specs
from aite.chaos.observer import ProbeConfig, observe_under_chaos
from aite.core.taskkit import DomainTaskKit, EventSink, TaskExecutor, TaskRunContext
from aite.storage.models import ReliabilityTaskRow
from aite.web.events import EventBus

_MAX_OBSERVE_DURATION = 600.0     # 观测时长上限（秒），与同步 REST 端点同口径


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


class ObserveTaskExecutor(TaskExecutor):
    """spec = observe body；result = {events, probes, rto, summary, probe}。"""

    async def execute(self, ctx: TaskRunContext) -> dict:
        return await run_observe(ctx.spec)


async def run_observe(body: dict) -> dict:
    """body → 校验 → 观测编排 → 附 probe 概要（REST 端点与域任务执行器共用）。"""
    specs, cfg = build_observe_cfg(body)
    result = await observe_under_chaos(
        specs, cfg, post_window=body.get("post_window"))
    result["probe"] = {"url": cfg.url, "model": cfg.model,
                       "interval": cfg.interval, "duration": cfg.duration}
    return result


def build_reliability_task_kit(bus: EventSink | None = None) -> DomainTaskKit:
    return DomainTaskKit("reliability", ReliabilityTaskRow, ObserveTaskExecutor(),
                         bus or EventBus())
