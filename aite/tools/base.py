"""工具适配层契约（平台化核心：外部评测工具统一接入）。

平台定位：精度基准委托 evalscope/opencompass 等专业工具计算，aite 负责
数据集管理、工具编排、结果统一入库（run_metric_summary），与自研引擎的
结果在 Web 趋势中并列可比。

适配器两步走：
- ``build_config``：平台统一 target/datasets 配置 → 工具原生配置
- ``run``：执行并收集，输出统一 ToolRunResult
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar


@dataclass
class ToolRunRequest:
    run_name: str
    target: dict                        # aite target 配置（openai_compatible 等）
    datasets: list[str]                 # 工具侧基准名（如 evalscope 的 ceval/mmlu）
    limit_per_subset: int | None = None  # 每科目条数（evalscope limit 语义）
    work_dir: Path = Path("./outputs")
    extra: dict = field(default_factory=dict)          # 透传工具原生参数
    metric_thresholds: dict[str, float] = field(default_factory=dict)
    auto_thresholds: bool = False  # metric_thresholds: auto → 按基准推荐阈值
    tests_dir: str | None = None    # pytest 引擎：用户自建功能测试目录（优先于内置场景）
    cases: list[str] | None = None  # pytest 引擎：用例 id 过滤（None = 全部；单用例复现场景）
    perf: dict = field(default_factory=dict)  # evalscope-perf 引擎：压测配置（prompt/parallel/number/...）


@dataclass
class ToolMetricRow:
    dataset: str            # "evalscope:ceval"
    metric: str             # "mean_acc"
    value: float
    count: int
    group: str | None = None
    threshold: float | None = None
    passed: bool | None = None
    higher_is_better: bool = True   # False = 延迟/错误率等越低越好（阈值方向 <=）


@dataclass
class ToolRunResult:
    rows: list[ToolMetricRow] = field(default_factory=list)
    report_files: dict[str, str] = field(default_factory=dict)   # 工具产出的报告文件
    raw_score: dict[str, float] = field(default_factory=dict)    # dataset -> 总分
    perf: dict[str, dict] = field(default_factory=dict)          # dataset -> 性能摘要
    failures: list[dict] = field(default_factory=list)           # 用例级失败详情 [{dataset, case, detail}]


class ToolAdapter(ABC):
    """外部评测工具适配器。子类注册后由 ``aite run -c`` 的 engine 字段路由。"""

    name: ClassVar[str]

    @abstractmethod
    def build_config(self, req: ToolRunRequest) -> dict:
        """平台配置 → 工具原生 run 配置（纯函数，可单测）。"""

    @abstractmethod
    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        """执行评测并收集结果（阻塞调用应在 to_thread 中进行）。"""

    def collect(self, req: ToolRunRequest) -> ToolRunResult:
        """收集工具产出 → 指标行/报告文件/失败明细（run 收尾统一调用）。"""
        raise NotImplementedError

    @abstractmethod
    def list_tasks(self) -> list[dict]:
        """枚举工具可用的基准/任务目录（name/metrics/tags 等，供前端选择）。"""


_TOOLS: dict[str, type[ToolAdapter]] = {}


def register_tool(name: str):
    def _wrap(cls: type[ToolAdapter]) -> type[ToolAdapter]:
        cls.name = name
        _TOOLS[name] = cls
        return cls
    return _wrap


def unregister_tool(name: str) -> bool:
    """注销引擎（测试隔离用；生产代码不卸载）。"""
    return _TOOLS.pop(name, None) is not None


def get_tool(name: str) -> ToolAdapter:
    try:
        return _TOOLS[name]()
    except KeyError:
        raise KeyError(f"unknown tool engine '{name}'; available: {sorted(_TOOLS)}") from None


def list_tools() -> list[str]:
    """已注册工具引擎名（REST /tools/engines 与前端引擎选择器数据源）。"""
    return sorted(_TOOLS)


def api_endpoint(target: dict, *, suffix: str = "/chat/completions") -> tuple[str, str]:
    """openai_compatible target → (完整请求 URL, api_key)。

    api_key 从 target.api_key_env 指定的环境变量读取，缺省 "EMPTY"（本地服务）。
    """
    if target.get("type", "openai_compatible") != "openai_compatible":
        raise ValueError("tool adapter 目前仅支持 openai_compatible target")
    if target.get("api_key"):
        # 显式优于隐式（CLAUDE.md 原则 5）：明文密钥禁入配置，静默忽略会让
        # 用户误以为已配好——联调时以 401 的形式才暴露，白烧一轮
        raise ValueError("target.api_key 不受支持（禁止明文密钥入配置/git）；"
                         "请删除该字段并改用 api_key_env: <环境变量名>")
    base = str(target.get("base_url", "")).rstrip("/")
    url = base if base.endswith(suffix) else base + suffix
    key = os.environ.get(target.get("api_key_env") or "OPENAI_API_KEY", "EMPTY")
    return url, key


def run_sync_in_thread(fn, *args, **kwargs):
    """阻塞的工具调用放入线程，避免卡死事件循环（Web 集成需要）。"""
    return asyncio.to_thread(fn, *args, **kwargs)
