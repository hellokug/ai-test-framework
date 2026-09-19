"""Abstract interfaces (signature-level contract). Core depends on nothing above it."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from aite.core.types import (
        EvalContext,
        InvokeOptions,
        MetricResult,
        RunReport,
        TargetResponse,
        TestCase,
    )


class TargetSystem(ABC):
    """A connector to the system-under-test (LLM API, agent framework, HTTP service)."""

    name: ClassVar[str] = "target"

    @abstractmethod
    async def invoke(self, case: TestCase, options: InvokeOptions) -> TargetResponse:
        """Execute one test case against the target."""

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class Dataset(ABC):
    """Streaming / samplable source of TestCases."""

    name: ClassVar[str] = "dataset"

    @abstractmethod
    def load(self, config: Any) -> AsyncIterator[TestCase]:
        """Yield test cases according to the dataset config."""
        raise NotImplementedError
        yield  # pragma: no cover


class Metric(ABC):
    """Rule / statistical metric over one case+response (optionally all variant responses).

    Contract:
    - ``value`` is normalized to 0~1 and higher is better (metrics whose raw
      meaning is "lower is better" must invert themselves, keeping raw values
      in ``raw``).
    - ``wants_variants``: engine passes ``responses`` = [(case, response), ...]
      of the base case AND its reliability variants (repeats/perturbations);
      compute() is then called once for the base case only.
    - ``aggregation``: how per-case values fold into the run summary.
    """

    name: ClassVar[str] = "metric"
    aggregation: ClassVar[str] = "mean"       # mean|rate|p50|p95|p99|min|max|sum
    higher_is_better: ClassVar[bool] = True
    wants_variants: ClassVar[bool] = False
    # 需要网关（LLM/embedding 调用）的指标置 True——engine 据此决定是否建 LLMGateway
    needs_llm: ClassVar[bool] = False

    threshold: float | None = None
    params: dict[str, Any]

    def __init__(self, threshold: float | None = None, **params: Any) -> None:
        self.threshold = threshold
        self.params = params

    @abstractmethod
    async def compute(
        self,
        *,
        case: TestCase,
        response: TargetResponse,
        responses: list[tuple[TestCase, TargetResponse]] | None = None,
        ctx: EvalContext,
    ) -> MetricResult:
        """Compute the metric for one executed case."""

    def judge_passed(self, value: float) -> bool | None:
        if self.threshold is None:
            return None
        return value >= self.threshold if self.higher_is_better else value <= self.threshold


class Judge(ABC):
    """LLM-as-a-Judge - routed through the gateway (own budget / rate limits).

    ``threshold`` is expressed on the judge's RAW scale (e.g. 1-10 rubric);
    ``scale_max`` (when set) lets the aggregator compare the normalized 0~1
    summary value against ``threshold / scale_max``.
    """

    name: ClassVar[str] = "judge"
    scale_max: float | None = None

    threshold: float | None = None
    weight: float = 1.0

    def __init__(self, threshold: float | None = None, weight: float = 1.0, **params: Any) -> None:
        self.threshold = threshold
        self.weight = weight
        self.params = params

    @property
    def normalized_threshold(self) -> float | None:
        if self.threshold is None:
            return None
        return self.threshold / self.scale_max if self.scale_max else self.threshold

    @abstractmethod
    async def judge(
        self,
        *,
        case: TestCase,
        response: TargetResponse,
        baseline: TargetResponse | None = None,
        ctx: EvalContext,
    ) -> MetricResult:
        """Judge one case; value normalized 0~1 (raw scores live in ``raw``)."""


class ReportRenderer(ABC):
    """Render a RunReport into a file. name matches suite ``report:`` entries."""

    name: ClassVar[str] = "renderer"

    @abstractmethod
    def render(self, report: RunReport, dest: Path) -> Path:
        ...


class ProgressReporter(ABC):
    """Progress abstraction (Rich bar / Noop / SSE push)."""

    @abstractmethod
    def start(self, total: int, description: str = "") -> None: ...

    @abstractmethod
    def update(self, n: int = 1, *, note: str | None = None) -> None: ...

    @abstractmethod
    def finish(self, message: str | None = None) -> None: ...


class Repository(ABC):
    """Persistence boundary (SQLAlchemy impl in aite.storage.repositories)."""

    @abstractmethod
    async def init(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    # -- suites / cases ------------------------------------------------------
    @abstractmethod
    async def upsert_suite(self, name: str, config: dict[str, Any] | None = None) -> str: ...

    @abstractmethod
    async def delete_suite(self, name: str) -> bool:
        """删除 suite；引用它的 run 解引用（suite_id 置 NULL）保留审计。"""

    @abstractmethod
    async def save_test_case(self, suite_id: str, case: TestCase) -> int: ...

    @abstractmethod
    async def list_test_cases(self, suite_id: str, limit: int = 200) -> list[dict]: ...

    # -- runs -----------------------------------------------------------------
    @abstractmethod
    async def create_run(self, run_name: str, config_snapshot: dict, suite_id: str | None = None) -> str: ...

    @abstractmethod
    async def update_run(self, run_id: str, **fields: Any) -> None: ...

    @abstractmethod
    async def get_run(self, run_id: str) -> dict | None: ...

    @abstractmethod
    async def list_runs(self, limit: int = 50, offset: int = 0, run_name: str | None = None) -> list[dict]: ...

    # -- case results / metrics ------------------------------------------------
    @abstractmethod
    async def save_case_result(
        self, run_id: str, case_row_id: int, case_id: str, response: TargetResponse,
        dataset: str | None = None,
    ) -> int: ...

    @abstractmethod
    async def list_case_results(self, run_id: str, limit: int | None = None) -> list[dict]: ...

    @abstractmethod
    async def save_metric_results(self, run_id: str, case_result_id: int, results: list[MetricResult]) -> None: ...

    @abstractmethod
    async def list_metric_results(self, run_id: str, metric: str | None = None) -> list[dict]: ...

    @abstractmethod
    async def save_metric_summary(self, run_id: str, summary: Any) -> None: ...

    @abstractmethod
    async def list_metric_summaries(self, run_id: str) -> list[dict]: ...

    @abstractmethod
    async def trends(self, run_name: str | None = None, metric: str | None = None,
                     days: int = 30) -> list[dict]: ...

    # -- model configs ----------------------------------------------------------
    @abstractmethod
    async def save_model_config(self, data: dict[str, Any]) -> str: ...

    @abstractmethod
    async def list_model_configs(self) -> list[dict]: ...

    @abstractmethod
    async def delete_model_config(self, model_id: str) -> bool: ...

    # -- domain tasks（四域独立任务表，契约见 docs/domain-backends.md §4/§6）----
    @abstractmethod
    async def create_domain_task(self, table: type[Any], spec: dict[str, Any],
                                 name: str = "", retry_of: str | None = None,
                                 retry_count: int = 0) -> dict: ...

    @abstractmethod
    async def update_domain_task(self, table: type[Any], task_id: str,
                                 expect_status: str | None = None, **fields: Any) -> dict | None:
        """条件更新：expect_status 给定时仅当前状态匹配才写入（状态转移竞态收口）。

        任务缺失或状态不匹配返回 None（调用方重读裁决）；终态自动补 finished_at。
        """

    @abstractmethod
    async def list_domain_tasks(self, table: type[Any], *, status: str | None = None,
                                retry_of: str | None = None, limit: int = 50) -> list[dict]: ...

    @abstractmethod
    async def get_domain_task(self, table: type[Any], task_id: str) -> dict | None: ...

    @abstractmethod
    async def delete_domain_task(self, table: type[Any], task_id: str) -> bool: ...

    @abstractmethod
    async def mark_stale_domain_tasks(self, table: type[Any]) -> int:
        """startup 收敛：running/stopping 残留 → stale（pending 为排队任务，保留）。"""


class NoopProgress(ProgressReporter):
    def start(self, total: int, description: str = "") -> None: pass
    def update(self, n: int = 1, *, note: str | None = None) -> None: pass
    def finish(self, message: str | None = None) -> None: pass
