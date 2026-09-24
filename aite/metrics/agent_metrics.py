"""Agent metrics: tool-call correctness, task completion, efficiency, trajectory."""

from __future__ import annotations

import json
from typing import ClassVar

from aite.core.interfaces import Metric
from aite.core.registry import register_metric
from aite.core.types import AgentTrace, MetricResult
from aite.metrics.llm_based import LLMMetricMixin, fmt_exc


def _trace_of(response) -> AgentTrace:
    if response.trace is not None:
        return response.trace
    raw_trace = (response.raw or {}).get("trace")
    if isinstance(raw_trace, dict):
        return AgentTrace.model_validate(raw_trace)
    return AgentTrace()


@register_metric("tool_call_correctness")
class ToolCallCorrectness(Metric):
    """Expected tool sequence vs actual tool calls (exact / set / subsequence mode).

    Expected via ``case.expected.tools``: ["search", "calculator"] or
    ``{"tools": [...], "mode": "ordered"|"unordered"|"subset"}``.
    """

    name: ClassVar[str] = "tool_call_correctness"
    aggregation: ClassVar[str] = "rate"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        spec = case.expected if isinstance(case.expected, dict) else {}
        expected = spec.get("tools") or (case.expected if isinstance(case.expected, list) else None)
        expected = [str(t) for t in (expected or [])]
        if not expected:
            return MetricResult(metric=self.name, value=0.0, error="no expected tools configured")
        mode = spec.get("mode") or self.params.get("mode", "ordered")
        trace = _trace_of(response)
        actual = [s.name for s in trace.tool_calls]
        if mode == "unordered":
            ok = sorted(actual) == sorted(expected)
            score = 1.0 if ok else _overlap(expected, actual)
        elif mode == "subset":
            ok = set(expected) <= set(actual)
            score = 1.0 if ok else _overlap(expected, actual)
        else:  # ordered - longest common subsequence ratio
            score = _lcs_ratio(expected, actual)
            ok = score >= 0.999
        return MetricResult(
            metric=self.name, value=round(score, 6),
            raw={"expected": expected, "actual": actual, "mode": mode, "exact": ok},
            passed=self.judge_passed(score) if self.threshold is not None else None)


@register_metric("tool_arg_correctness")
class ToolArgCorrectness(Metric):
    """Expected key arguments vs actual tool-call arguments (case-insensitive substring).

    Expected via ``case.expected.tool_args``: ``{"calculator": ["85", "rate"],
    "search": ["deposit insurance"]}`` — each named tool must have been called
    with args containing each needle. Uncalled tools count all needles as miss.
    """

    name: ClassVar[str] = "tool_arg_correctness"
    aggregation: ClassVar[str] = "rate"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        spec = case.expected if isinstance(case.expected, dict) else {}
        expected_args = spec.get("tool_args") or self.params.get("tool_args") or {}
        if not expected_args:
            return MetricResult(metric=self.name, value=0.0,
                                error="no expected tool_args configured")
        trace = _trace_of(response)
        calls: dict[str, list[str]] = {}
        for step in trace.tool_calls:
            args = step.input if isinstance(step.input, str) else json.dumps(
                step.input or {}, ensure_ascii=False)
            calls.setdefault(step.name.lower(), []).append(args.lower())
        checks = []
        for tool, needles in expected_args.items():
            got = calls.get(str(tool).lower(), [])
            for needle in needles or []:
                checks.append({"tool": str(tool), "needle": str(needle),
                               "hit": any(str(needle).lower() in a for a in got)})
        if not checks:
            return MetricResult(metric=self.name, value=0.0,
                                error="expected tool_args has no needles")
        value = sum(1 for c in checks if c["hit"]) / len(checks)
        return MetricResult(
            metric=self.name, value=round(value, 6), raw={"checks": checks},
            passed=self.judge_passed(value) if self.threshold is not None else None)


def _overlap(expected: list[str], actual: list[str]) -> float:
    if not expected:
        return 0.0
    hits = sum(1 for t in expected if t in actual)
    return hits / len(expected)


def _as_float(value, default: float = 0.0) -> float:
    """judge 回传的数值字段可能是字符串/None——宽松解析，坏值回退默认。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _lcs_ratio(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1] / len(a)


@register_metric("task_completion")
class TaskCompletion(Metric, LLMMetricMixin):
    """Goal achieved? Rule mode checks output contains expected markers; LLM mode
    (gateway available) asks the judge model yes/no + progress 0~1."""

    name: ClassVar[str] = "task_completion"
    aggregation: ClassVar[str] = "mean"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        if not response.ok:
            return MetricResult(metric=self.name, value=0.0, passed=False,
                                error=(response.error or "")[:200])
        spec = case.expected if isinstance(case.expected, dict) else {}
        markers = spec.get("success_markers") or self.params.get("success_markers")
        if markers:
            text = response.output_text.lower()
            hits = sum(1 for m in markers if str(m).lower() in text)
            value = hits / len(markers)
            return MetricResult(metric=self.name, value=round(value, 6),
                                raw={"mode": "markers", "hits": hits},
                                passed=self.judge_passed(value) if self.threshold is not None else None)
        # LLM mode
        goal = case.input if isinstance(case.input, str) else str(case.input)
        user = (
            f"Task given to an AI agent:\n{goal}\n\n"
            f"Agent final output:\n{response.output_text[:4000]}\n\n"
            f"Tool calls made: {[s.name for s in _trace_of(response).tool_calls]}\n\n"
            'Did the agent complete the task? JSON only: '
            '{"completed": true|false, "progress": <0.0-1.0>, "reasoning": "<brief>"}'
        )
        try:
            data = await self.llm_json(ctx, "You evaluate agent task completion.", user,
                                       model=self.params.get("model"))
        except Exception as exc:  # noqa: BLE001
            return MetricResult(metric=self.name, value=0.0, error=f"llm mode failed: {fmt_exc(exc)}")
        if data is None:
            return MetricResult(metric=self.name, value=0.0, error="unparseable judge output")
        raw_progress = data.get("progress")
        value = (1.0 if data.get("completed") else 0.0) if raw_progress is None \
            else _as_float(raw_progress)
        value = max(0.0, min(value, 1.0))
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"mode": "llm", "completed": bool(data.get("completed")),
                                 "reasoning": data.get("reasoning", "")},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("agent_efficiency")
class AgentEfficiency(Metric):
    """Step economy: normalized score penalizing redundant tool calls.

    value = min(1, expected_steps / actual_steps) * (1 - redundant_ratio)
    """

    name: ClassVar[str] = "agent_efficiency"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        trace = _trace_of(response)
        spec = case.expected if isinstance(case.expected, dict) else {}
        expected_steps = int(spec.get("expected_steps") or self.params.get("expected_steps", 3))
        tools = trace.tool_calls
        actual_steps = max(len(trace.steps), 1)
        names = [s.name for s in tools]
        redundant = len(names) - len(set(names))
        redundant_ratio = redundant / len(names) if names else 0.0
        step_score = min(1.0, expected_steps / actual_steps)
        value = step_score * (1.0 - redundant_ratio)
        return MetricResult(
            metric=self.name, value=round(value, 6),
            raw={"steps": len(trace.steps), "tool_calls": len(tools),
                 "redundant_tool_calls": redundant, "expected_steps": expected_steps},
            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("trajectory_quality")
class TrajectoryQuality(Metric, LLMMetricMixin):
    """Judge-scored quality of the whole agent trajectory (steps + final answer)."""

    name: ClassVar[str] = "trajectory_quality"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        trace = _trace_of(response)
        if not trace.steps:
            return MetricResult(metric=self.name, value=0.0, error="no trace captured")
        steps_text = "\n".join(
            f"- [{s.type}] {s.name}: {str(s.input)[:120]} -> {str(s.output)[:120]}"
            + (f" (error: {s.error})" if s.error else "")
            for s in trace.steps[:40]
        )
        goal = case.input if isinstance(case.input, str) else str(case.input)
        user = (
            f"Task:\n{goal}\n\nTrajectory:\n{steps_text}\n\nFinal answer:\n"
            f"{response.output_text[:2000]}\n\n"
            'Rate trajectory quality (rational ordering, correct tool use, no loops, '
            'task completion) 1-10. JSON only: {"score": <1-10>, "reasoning": "<brief>"}'
        )
        try:
            data = await self.llm_json(ctx, "You are an expert agent-trajectory evaluator.",
                                       user, model=self.params.get("model"))
        except Exception as exc:  # noqa: BLE001
            return MetricResult(metric=self.name, value=0.0, error=f"llm mode failed: {fmt_exc(exc)}")
        if not data:
            return MetricResult(metric=self.name, value=0.0, error="unparseable judge output")
        try:
            score = max(0.0, min(float(data.get("score", 0)), 10.0))
        except (TypeError, ValueError):
            score = 0.0
        value = score / 10.0
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"score": score, "reasoning": data.get("reasoning", "")},
                            passed=self.judge_passed(value) if self.threshold is not None else None)
