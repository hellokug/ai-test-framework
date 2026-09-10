"""可靠性 chaos 框架（模块 I）核心契约：故障声明 / 运行时句柄 / 事件记录。

五能力映射（roadmap 模块 I）：
- I1 故障对象感知 → Injector 对 target 的解析与 describe
- I2 故障注入 → Injector.inject
- I3 检测 → Injector.verify（故障生效确认：注入失败与无效应区分）
- I4 恢复 → Injector.recover + controller 的 finally 兜底（防故障泄漏）
- I5 业务观测 → FaultEvent 时间线（注入/生效/恢复时间戳），与评测 run 的
  timeseries 曲线叠加（故障窗口标注）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FaultSpec:
    """故障声明（suite yaml chaos 段的一条 / REST payload 的一条）。

    kind 语义由注入器定义（如 toxiproxy: latency/timeout/blackhole，
    process: kill/suspend），injector 决定由哪个注入器执行。
    """

    injector: str                     # 注入器名（注册表键）
    kind: str                         # 故障类型（注入器内语义）
    target: dict[str, Any]            # 故障对象（注入器各自 schema：url/pid/port...）
    params: dict[str, Any] = field(default_factory=dict)   # 故障参数（延迟毫秒等）
    name: str = ""                    # 可读名（缺省 injector:kind）

    def resolve_name(self) -> str:
        return self.name or f"{self.injector}:{self.kind}"

    @classmethod
    def from_dict(cls, raw: dict) -> FaultSpec:
        try:
            return cls(injector=str(raw["injector"]), kind=str(raw["kind"]),
                       target=dict(raw.get("target") or {}),
                       params=dict(raw.get("params") or {}),
                       name=str(raw.get("name") or ""))
        except (KeyError, TypeError) as exc:
            raise ValueError(f"chaos.fault 缺少必要字段（injector/kind）: {exc}") from exc


@dataclass
class FaultHandle:
    """注入成功的运行时句柄（恢复的唯一凭据；由各注入器自定义内容）。"""

    spec: FaultSpec
    ref: dict[str, Any]               # 注入器私有引用（toxic 名/blade uid/pid 等）
    injected_at: float                # epoch 秒（time.time()）


@dataclass
class FaultEvent:
    """故障生命周期事件（I5 观测时间线的一条）。"""

    fault: str                        # FaultSpec.resolve_name()
    phase: str = ""                   # injected|verified|verify_failed|recovered|recover_failed
    at: float = 0.0
    detail: str = ""

    def to_dict(self) -> dict:
        return {"fault": self.fault, "phase": self.phase,
                "at": self.at, "detail": self.detail}


def validate_specs(raw: Any) -> list[FaultSpec]:
    """chaos 段 → FaultSpec 列表（fail-fast；空/缺省返回空列表）。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("chaos 段须为 fault 声明数组")
    return [FaultSpec.from_dict(item) for item in raw]
