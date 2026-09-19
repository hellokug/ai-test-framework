"""Core data types shared across engine / metrics / storage / reporting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from aite.gateway.llm_gateway import LLMGateway


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------- 四域任务状态机
# 单一来源（原则 8）：core/taskkit.py、storage/repositories.py、域路由共用，
# 禁止他处再定义副本。状态机与语义见 docs/domain-backends.md §3。
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_STOPPING = "stopping"          # 协作式停止过渡态，超时兜底强转 cancelled
TASK_SUCCEEDED = "succeeded"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"
TASK_STALE = "stale"                # 进程死亡残留，startup 扫描收敛
TASK_TERMINAL = frozenset({TASK_SUCCEEDED, TASK_FAILED, TASK_CANCELLED, TASK_STALE})


class Timings(BaseModel):
    ttft_ms: float | None = None      # time to first token (streaming)
    total_ms: float | None = None
    tokens_per_sec: float | None = None


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class AgentTraceStep(BaseModel):
    type: str = "llm"                 # llm | tool | retrieval | error
    name: str = ""
    input: Any = None
    output: Any = None
    duration_ms: float | None = None
    error: str | None = None


class AgentTrace(BaseModel):
    steps: list[AgentTraceStep] = Field(default_factory=list)

    @property
    def tool_calls(self) -> list[AgentTraceStep]:
        return [s for s in self.steps if s.type == "tool"]

    @property
    def llm_calls(self) -> list[AgentTraceStep]:
        return [s for s in self.steps if s.type == "llm"]

    @property
    def errors(self) -> list[AgentTraceStep]:
        return [s for s in self.steps if s.type == "error" or s.error]


class TestCase(BaseModel):
    id: str
    input: Any
    expected: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_context: list[str] | None = None    # RAG hook: gold/expected contexts
    # reliability support (engine materializes derived cases)
    variant: str = "base"                          # base | repeat-N | perturb-<kind>-N
    variant_of: str | None = None                  # original case id
    dataset: str | None = None                     # dataset id/path the case came from


class TargetResponse(BaseModel):
    output: Any = None
    usage: TokenUsage | None = None
    timings: Timings = Field(default_factory=Timings)
    cost_usd: float | None = None
    trace: AgentTrace | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None             # connector-specific extras

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def output_text(self) -> str:
        if self.output is None:
            return ""
        if isinstance(self.output, str):
            return self.output
        if isinstance(self.output, dict):
            for key in ("output", "text", "answer", "content", "result"):
                if key in self.output and isinstance(self.output[key], str):
                    return self.output[key]
            import json
            return json.dumps(self.output, ensure_ascii=False)
        return str(self.output)


class InvokeOptions(BaseModel):
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    stream: bool | None = None
    seed: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class MetricResult(BaseModel):
    metric: str
    value: float                      # normalized 0~1, higher is better
    raw: dict[str, Any] = Field(default_factory=dict)
    passed: bool | None = None
    error: str | None = None


class CaseMetricRecord(BaseModel):
    case_id: str
    metric: str
    value: float | None = None
    passed: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    dataset: str | None = None
    group: str | None = None


class MetricSummary(BaseModel):
    metric: str
    dataset: str | None = None
    group: str | None = None
    aggregation: str = "mean"
    value: float | None = None
    threshold: float | None = None
    higher_is_better: bool = True
    passed: bool | None = None
    count: int = 0
    pass_rate: float | None = None
    values_min: float | None = None
    values_max: float | None = None


class RunReport(BaseModel):
    run_id: str | None = None
    run_name: str
    status: str = "running"           # passed | failed | error
    pass_strategy: str = "all_of"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_cases: int = 0
    error_cases: int = 0
    total_cost_usd: float = 0.0
    summaries: list[MetricSummary] = Field(default_factory=list)
    case_metrics: list[CaseMetricRecord] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return 0 if self.status == "passed" else 1

    def summary_for(self, metric: str) -> MetricSummary | None:
        for s in self.summaries:
            if s.metric == metric and s.group is None and s.dataset is None:
                return s
        return None


class EvalContext(BaseModel):
    """Services handed to metrics/judges during Phase B (never None-safe fields optional)."""

    model_config: ClassVar[dict] = {"arbitrary_types_allowed": True}

    run_name: str = ""
    run_id: str | None = None
    gateway: Any | None = None        # LLMGateway | None (LLM/embedding calls)
    config: dict[str, Any] = Field(default_factory=dict)

    async def llm_complete(
        self, messages: list[dict[str, str]], *, model: str | None = None,
        temperature: float = 0.0, max_tokens: int = 1024, json_mode: bool = False,
    ) -> str:
        """Complete via the run's gateway; raises if no gateway configured."""
        gateway: LLMGateway | None = self.gateway  # type: ignore[assignment]
        if gateway is None:
            raise RuntimeError("no LLM gateway configured for LLM-based metrics")
        result = await gateway.complete(
            messages, model=model, temperature=temperature, max_tokens=max_tokens,
            json_mode=json_mode,
        )
        return result.text

    async def embed(self, texts: list[str]) -> list[list[float]]:
        gateway: LLMGateway | None = self.gateway  # type: ignore[assignment]
        if gateway is None:
            from aite.utils.text import hash_embedding
            return [hash_embedding(t) for t in texts]
        return await gateway.embed(texts)
