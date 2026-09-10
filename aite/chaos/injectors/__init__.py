"""故障注入器实现（导入即注册；缺依赖的注入器在此层兜底不炸包导入）。"""

from aite.chaos.injectors import (
    process,  # noqa: F401  (registers)
    toxiproxy,  # noqa: F401  (registers)
)
