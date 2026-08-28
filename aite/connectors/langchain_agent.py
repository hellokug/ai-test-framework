"""LangChain / LangGraph agent connector (requires aite[agent]).

Config example::

    target:
      type: langchain_agent
      kwargs:
        module: myapp.agents          # module path exposing build_agent() / AGENT
        attr: AGENT                    # optional attribute name (default build_agent)

The case input (str or messages list) is passed to the runnable's ``ainvoke``;
``astream_events`` captures the trajectory into ``AgentTrace`` when available.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, ClassVar

from aite.connectors.base import Stopwatch, case_to_messages, truncate_error
from aite.connectors.tracing import LangChainCallbackHandler, trace_from_langgraph_events
from aite.core.interfaces import TargetSystem
from aite.core.registry import register_connector
from aite.core.types import AgentTrace, TargetResponse

if TYPE_CHECKING:
    from aite.core.loader import TargetConfig
    from aite.core.types import InvokeOptions, TestCase


def _load_runnable(kwargs: dict[str, Any]):
    module = importlib.import_module(kwargs["module"])
    attr = kwargs.get("attr", "build_agent")
    obj = getattr(module, attr)
    return obj() if callable(obj) and not hasattr(obj, "ainvoke") else obj


@register_connector("langchain_agent")
class LangChainAgentConnector(TargetSystem):
    name: ClassVar[str] = "langchain_agent"

    def __init__(self, config: TargetConfig) -> None:
        try:
            import langchain_core  # noqa: F401
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ImportError(
                "langchain_agent connector requires the [agent] extra: "
                "pip install 'aite[agent]'"
            ) from exc
        self.config = config
        self.kwargs = dict(config.params or {}) or dict(config.template or {})
        self.runnable = _load_runnable(self.kwargs)

    async def invoke(self, case: TestCase, options: InvokeOptions) -> TargetResponse:
        watch = Stopwatch()
        handler = LangChainCallbackHandler()
        config = {"callbacks": [handler]}
        if options.max_tokens:
            config["max_tokens"] = options.max_tokens  # type: ignore[dict-item]
        try:
            messages = case_to_messages(case, options)
            use_events = getattr(self.runnable, "astream_events", None) is not None
            if use_events and self.kwargs.get("stream_events", True):
                events: list[dict] = []
                final: Any = None
                async for ev in self.runnable.astream_events(
                    {"messages": messages} if self.kwargs.get("graph_input") else messages,
                    version="v2",
                ):
                    events.append(ev)
                    if ev.get("event") == "on_chain_end" and ev.get("name") in ("agent", "graph"):
                        final = ev.get("data", {}).get("output")
                output = final if final is not None else events
                trace = trace_from_langgraph_events(events)
            else:
                result = await self.runnable.ainvoke(messages, config)
                output = _unwrap(result)
                trace = handler.trace
            watch.mark_first_token()
            return TargetResponse(
                output=output,
                timings=watch.finish(),
                trace=trace,
                raw={"retrieval_context": (case.retrieval_context or [])},
            )
        except Exception as exc:  # noqa: BLE001
            return TargetResponse(error=truncate_error(exc), trace=AgentTrace())


def _unwrap(result: Any) -> Any:
    if isinstance(result, str):
        return result
    if hasattr(result, "content"):        # AIMessage
        return result.content
    if isinstance(result, dict):
        for key in ("output", "answer", "text", "result"):
            if key in result:
                return result[key]
        return result
    if hasattr(result, "return_values"):  # AgentFinish
        return result.return_values.get("output")
    return str(result)
