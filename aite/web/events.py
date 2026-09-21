"""SSE 事件总线（兼容 shim）——实现已迁至 aite/events.py（§13 分层整理：
service/域层不再反向依赖 web 包）。"""

from aite.events import EventBus  # noqa: F401  (兼容再导出)
