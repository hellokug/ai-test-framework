"""可靠性 chaos 框架（模块 I 骨架）。

调研结论（2026-09）：ChaosBlade 可集成为进程/资源层引擎（Linux 执行机），
Toxiproxy 为网络层引擎（用户态跨平台，首选）；Chaos Mesh / Litmus 强依赖
K8s 排除。自研部分 = aite 语义层的故障编排（生命周期/生效确认/兜底恢复/
业务观测时间线）。

一期实现：toxiproxy（网络 latency/timeout/bandwidth）+ process（kill/
suspend）两个注入器 + run_with_chaos 编排器；ChaosBlade 包装为二期注入器
（注册表开闭点已留：新增 injectors/blade.py 即可）。
"""

from aite.chaos.controller import ChaosVerifyError, run_with_chaos
from aite.chaos.discovery import discover_targets as discover_targets
from aite.chaos.injectors.base import (
    get_injector as get_injector,
)
from aite.chaos.injectors.base import (
    list_injectors as list_injectors,
)
from aite.chaos.injectors.base import (
    register_injector as register_injector,
)
from aite.chaos.injectors.base import (
    unregister_injector as unregister_injector,
)
from aite.chaos.model import (
    FaultEvent as FaultEvent,
)
from aite.chaos.model import (
    FaultHandle as FaultHandle,
)
from aite.chaos.model import (
    FaultSpec as FaultSpec,
)
from aite.chaos.model import (
    validate_specs as validate_specs,
)
from aite.chaos.observer import observe_under_chaos as observe_under_chaos

__all__ = ["ChaosVerifyError", "FaultEvent", "FaultHandle", "FaultSpec",
           "discover_targets", "get_injector", "list_injectors",
           "observe_under_chaos", "register_injector", "run_with_chaos",
           "unregister_injector", "validate_specs"]
