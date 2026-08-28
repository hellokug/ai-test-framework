"""Agent tracing helpers.

- ``LangChainCallbackHandler``: BaseCallbackHandler capturing LLM/tool steps.
- ``RAGHookMixin``: connectors of RAG systems can call ``self.report_retrieval()``
  to attach retrieved contexts to the response (source #3 for RAG metrics).
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from aite.core.types import AgentTrace, AgentTraceStep


class LangChainCallbackHandler:
    """Builds an AgentTrace from langchain callback events (no hard dependency)."""

    def __init__(self) -> None:
        self.trace = AgentTrace()
        self._starts: dict[int, float] = {}

    # -- LLM ---------------------------------------------------------------
    def on_llm_start(self, serialized, prompts, **kwargs: Any) -> None:
        self._starts[id(prompts)] = time.perf_counter()
        self.trace.steps.append(AgentTraceStep(
            type="llm", name=_name(serialized) or "llm",
            input={"prompts": [str(p)[:2000] for p in (prompts or [])]},
        ))

    def on_llm_end(self, response, **kwargs: Any) -> None:
        start = self._starts.pop(id(kwargs.get("prompts", [])), None)
        text = ""
        with contextlib.suppress(AttributeError, IndexError, TypeError):
            text = response.generations[0][0].text
        step = self.trace.steps[-1] if self.trace.steps and self.trace.steps[-1].type == "llm" \
            else AgentTraceStep(type="llm")
        step.output = text[:4000]
        step.duration_ms = round((time.perf_counter() - start) * 1000, 3) if start else None
        if step not in self.trace.steps:
            self.trace.steps.append(step)

    def on_llm_error(self, error, **kwargs: Any) -> None:
        self.trace.steps.append(AgentTraceStep(type="error", name="llm", error=str(error)[:2000]))

    # -- tools ---------------------------------------------------------------
    def on_tool_start(self, serialized, input_str: str, **kwargs: Any) -> None:
        self._starts[hash(input_str)] = time.perf_counter()
        self.trace.steps.append(AgentTraceStep(
            type="tool", name=_name(serialized) or kwargs.get("name") or "tool",
            input=str(input_str)[:2000],
        ))

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        key = kwargs.get("input_str")
        start = self._starts.pop(hash(key), None) if key else None
        if self.trace.steps and self.trace.steps[-1].type == "tool":
            step = self.trace.steps[-1]
        else:
            step = AgentTraceStep(type="tool", name="tool")
            self.trace.steps.append(step)
        step.output = str(output)[:4000]
        step.duration_ms = round((time.perf_counter() - start) * 1000, 3) if start else None

    def on_tool_error(self, error, **kwargs: Any) -> None:
        self.trace.steps.append(AgentTraceStep(type="error", name="tool", error=str(error)[:2000]))

    # -- chains ---------------------------------------------------------------
    def on_chain_start(self, serialized, inputs, **kwargs: Any) -> None:
        if kwargs.get("name") in ("AgentExecutor",):
            self._starts["chain"] = time.perf_counter()

    def on_chain_end(self, outputs, **kwargs: Any) -> None:
        self._starts.pop("chain", None)


def _name(serialized: Any) -> str:
    if isinstance(serialized, dict):
        kw = serialized.get("kwargs") or {}
        if isinstance(kw, dict):
            return str(kw.get("name") or serialized.get("name") or "")
        return str(serialized.get("name") or "")
    return ""


class RAGHookMixin:
    """Mixin for connectors that also perform retrieval.

    Usage inside a connector::

        contexts = self.report_retrieval(documents)
        response.raw = {"retrieval_context": contexts}
    """

    def report_retrieval(self, documents: list[Any]) -> list[str]:
        return [d if isinstance(d, str) else str(getattr(d, "page_content", d)) for d in documents]


def trace_from_langgraph_events(events: list[dict]) -> AgentTrace:
    """Convert LangGraph astream_events snapshots into an AgentTrace."""
    trace = AgentTrace()
    for ev in events:
        kind = ev.get("event")
        if kind == "on_chat_model_start":
            trace.steps.append(AgentTraceStep(type="llm", name=ev.get("name", "llm")))
        elif kind == "on_chat_model_end":
            trace.steps.append(AgentTraceStep(type="llm", name=ev.get("name", "llm"),
                                              output=str(ev.get("data", ""))[:4000]))
        elif kind == "on_tool_start":
            trace.steps.append(AgentTraceStep(
                type="tool", name=ev.get("name", "tool"),
                input=str(ev.get("data", ""))[:2000]))
        elif kind == "on_tool_end":
            trace.steps.append(AgentTraceStep(
                type="tool", name=ev.get("name", "tool"),
                output=str(ev.get("data", ""))[:4000]))
    return trace
