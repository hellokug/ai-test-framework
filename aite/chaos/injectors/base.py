"""故障注入器：抽象契约 + 注册表（开闭原则：新故障类型 = 新注入器文件）。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

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


def list_weapons() -> list[dict]:
    """武器目录（自描述）：注入器 + kinds 字符串表（既有消费方兼容）+ 逐 kind
    参数 schema（§14.2，用例 params 填写依据）。"""
    out = []
    for name in sorted(_INJECTORS):
        cls = _INJECTORS[name]
        out.append({"injector": name, "kinds": sorted(cls.kinds),
                    "schemas": {kind: cls.param_schemas.get(kind, {})
                                for kind in sorted(cls.kinds)}})
    return out


_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool}


def validate_params(injector: str, kind: str,
                    schemas: dict[str, dict], params: dict[str, Any]) -> None:
    """params → schema 校验（§14.2）：未知/缺失/类型/范围 fail-fast 显式报错。"""
    schema = schemas.get(kind)
    if schema is None:
        if params:
            raise ValueError(f"{injector}.{kind} 不接受 params: {sorted(params)}")
        return
    for key, value in (params or {}).items():
        if key not in schema:
            raise ValueError(f"{injector}.{kind} 未知参数 {key!r}"
                             f"（可用: {sorted(schema)}）")
        spec = schema[key]
        type_name = spec.get("type", "str")
        if type_name != "bool" and isinstance(value, bool):
            raise ValueError(f"{injector}.{kind}.{key} 期望类型 {type_name}, "
                             f"实际 bool")
        py_type = _TYPES.get(type_name)
        if py_type and not isinstance(value, py_type):
            raise ValueError(f"{injector}.{kind}.{key} 期望类型 {type_name}, "
                             f"实际 {type(value).__name__}")
        if "min" in spec and value < spec["min"]:
            raise ValueError(f"{injector}.{kind}.{key} 低于下限 "
                             f"{spec['min']}: {value}")
        if "max" in spec and value > spec["max"]:
            raise ValueError(f"{injector}.{kind}.{key} 超过上限 "
                             f"{spec['max']}: {value}")
    for key, spec in schema.items():
        if spec.get("required") and key not in (params or {}):
            raise ValueError(f"{injector}.{kind} 缺必填参数 {key!r}"
                             f"（可用: {sorted(schema)}）")


class Injector(ABC):
    """故障注入器契约：注入 → 生效确认 → 恢复（三段生命周期，I2/I3/I4）。

    实现约束：
    - inject 必须返回 FaultHandle（恢复的唯一凭据），注入失败抛异常
    - verify 必须区分「注入动作失败」与「故障无效应」（返回 False）
    - recover 必须幂等（controller 的兜底恢复可能重复调用）
    - 全部实现为 async（网络型注入器走 REST；本地型亦统一签名）
    - kinds: 注入器支持的故障类型清单（武器目录数据源）
    - param_schemas: kind → {参数名: {type/required/min/max/default}}（§14.2，
      inject 入口须调用 validate_params 校验）
    """

    name: ClassVar[str]
    kinds: ClassVar[list[str]] = []
    param_schemas: ClassVar[dict[str, dict]] = {}

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
