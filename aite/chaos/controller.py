"""Chaos 编排器：注入 → 生效确认 → 业务执行 → 兜底恢复（I2/I3/I4 编排层）。"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from aite.chaos.injectors.base import get_injector
from aite.chaos.model import FaultEvent, FaultSpec, validate_specs
from aite.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class ChaosVerifyError(RuntimeError):
    """故障注入后生效确认失败（已注入部分会被兜底回收）。"""


async def run_with_chaos(specs_raw: list[dict] | list[FaultSpec] | None,
                         business: Callable[[], Awaitable[T]],
                         ) -> tuple[T, list[dict]]:
    """在故障注入下执行业务协程，返回 (业务结果, 事件时间线)。

    顺序：逐条注入并确认生效（任一失败 → 回滚已注入并抛 ChaosVerifyError）
    → 业务执行 → finally 逆序恢复全部（幂等 recover，恢复失败仅告警不阻断——
    防故障泄漏优先，泄漏需人工跟进故必须显式告警）。
    """
    specs: list[FaultSpec] = validate_specs(specs_raw)
    events: list[dict] = []

    def event(fault: str, phase: str, detail: str = "") -> None:
        events.append(FaultEvent(fault=fault, phase=phase,
                                 at=time.time(), detail=detail).to_dict())

    handles: list[tuple[Any, Any]] = []
    try:
        for spec in specs:
            injector = get_injector(spec.injector)
            handle = await injector.inject(spec)
            handles.append((injector, handle))
            event(spec.resolve_name(), "injected")
            if await injector.verify(handle):
                event(spec.resolve_name(), "verified")
            else:
                event(spec.resolve_name(), "verify_failed",
                      "故障未生效（注入动作成功但效果不可确认）")
                raise ChaosVerifyError(
                    f"故障 {spec.resolve_name()} 注入后未确认生效")
        result = await business()
        return result, events
    finally:
        # 逆序兜底恢复：业务成功/异常/确认失败都必经此处（防故障泄漏）
        for injector, handle in reversed(handles):
            name = handle.spec.resolve_name()
            try:
                await injector.recover(handle)
                event(name, "recovered")
            except Exception as exc:  # noqa: BLE001 - 恢复失败必须显式告警
                logger.exception("chaos 恢复失败 %s（防泄漏需人工确认）", name)
                event(name, "recover_failed", str(exc))
        if handles:
            logger.info("chaos 故障已全部回收（%d 条）", len(handles))
