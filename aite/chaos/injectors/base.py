"""故障注入器：抽象契约 + 注册表（开闭原则：新故障类型 = 新注入器文件）。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import ClassVar

from aite.chaos.model import FaultHandle, FaultSpec
from aite.utils.logging import get_logger

logger = get_logger(__name__)

_INJECTORS: dict[str, type[Injector]] = {}


def register_injector(name: str):
    def _wrap(cls: type[Injector]) -> type[Injector]:
        cls.name = name
        _INJECTORS[name] = cls
        return cls
    return _wrap


def unregister_injector(name: str) -> bool:
    """注销（测试隔离用）。"""
    return _INJECTORS.pop(name, None) is not None


def get_injector(name: str) -> Injector:
    try:
        return _INJECTORS[name]()
    except KeyError:
        raise KeyError(f"unknown chaos injector '{name}'; "
                       f"available: {sorted(_INJECTORS)}") from None


def list_injectors() -> list[str]:
    return sorted(_INJECTORS)


class Injector(ABC):
    """故障注入器契约：注入 → 生效确认 → 恢复（三段生命周期，I2/I3/I4）。

    实现约束：
    - inject 必须返回 FaultHandle（恢复的唯一凭据），注入失败抛异常
    - verify 必须区分「注入动作失败」与「故障无效应」（返回 False）
    - recover 必须幂等（controller 的兜底恢复可能重复调用）
    - 全部实现为 async（网络型注入器走 REST；本地型亦统一签名）
    """

    name: ClassVar[str]

    @abstractmethod
    async def inject(self, spec: FaultSpec) -> FaultHandle: ...

    @abstractmethod
    async def verify(self, handle: FaultHandle) -> bool:
        """故障生效确认：True = 故障正在生效。"""

    @abstractmethod
    async def recover(self, handle: FaultHandle) -> None: ...

    @staticmethod
    def now() -> float:
        return time.time()
