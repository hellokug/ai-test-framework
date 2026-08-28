"""ExecutionEngine: two-phase async orchestration.

Phase A - execute: dataset cases (with reliability variants materialized) are
invoked concurrently (semaphore + rpm limiter + per-case timeout + retries in
connectors); responses are persisted.
Phase B - evaluate: metrics computed per base case (variant-aware metrics get
the whole variant group); judges run through the gateway (own rate limits and
cost circuit breaker). Aggregation -> thresholds -> pass/fail -> persistence ->
report rendering.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aite.core.aggregator import aggregate, decide_status
from aite.core.context import build_context, build_gateway
from aite.core.registry import (
    load_plugins,
    registry,
    resolve_connector,
    resolve_dataset,
    resolve_judge,
    resolve_metric,
    resolve_renderer,
)
from aite.core.types import (
    CaseMetricRecord,
    InvokeOptions,
    MetricResult,
    RunReport,
    TargetResponse,
    TestCase,
    utcnow,
)
from aite.gateway.llm_gateway import CostLimitExceeded, InProcessRateLimiter
from aite.utils.async_utils import with_timeout
from aite.utils.logging import get_logger
from aite.utils.text import perturb_text, stable_seed

if TYPE_CHECKING:
    from aite.core.interfaces import ProgressReporter, Repository
    from aite.core.loader import SuiteConfig

logger = get_logger("engine")

# ensure builtin components are registered on first engine use
import aite.connectors  # noqa: E402,F401  (registers connectors)
import aite.datasets  # noqa: E402,F401  (registers datasets)
import aite.metrics  # noqa: E402,F401  (registers metrics + judges)
import aite.reporting  # noqa: E402,F401  (registers renderers)


class ExecutionEngine:
    """Orchestrates one suite run end-to-end."""

    def __init__(self, repo: Repository, progress: ProgressReporter | None = None,
                 event_cb: Callable[[dict], None] | None = None) -> None:
        self.repo = repo
        self.progress = progress
        self.event_cb = event_cb

    # ------------------------------------------------------------------ public
    async def run_suite(
        self,
        config: SuiteConfig,
        *,
        limit: int | None = None,
        dataset_ids: list[str] | None = None,
        render: bool = True,
        connector_override: Any = None,
    ) -> RunReport:
        load_plugins(config.plugins)
        report = RunReport(
            run_name=config.run_name, pass_strategy=config.pass_strategy,
            started_at=utcnow(), config=config.model_dump(mode="json", exclude={"storage"}),
        )
        self._emit("run_started", run_name=config.run_name)
        suite_id = await self.repo.upsert_suite(config.run_name, config.snapshot())
        run_id = await self.repo.create_run(config.run_name, config.snapshot(), suite_id)
        report.run_id = run_id

        gateway = build_gateway(config)
        ctx = build_context(config, gateway, run_id)
        target = connector_override or self._build_target(config)

        try:
            cases = await self._load_cases(config, limit, dataset_ids)
            report.total_cases = len({c.id for c in cases})
            await self.repo.update_run(run_id, total_cases=report.total_cases)

            # ---------------- Phase A: execute ----------------
            executed = await self._execute_all(config, target, cases, run_id, report)

            # ---------------- Phase B: evaluate ----------------
            records = await self._evaluate(config, executed, ctx, report)

            summaries = aggregate(records, self._metric_infos(config))
            report.summaries = summaries
            status, score = decide_status(summaries, config.pass_strategy, config.weights)
            report.status = status
            report.details["overall_score"] = score
            report.details["gateway_stats"] = gateway.stats() if gateway else None
            report.finished_at = utcnow()
            report.case_metrics = records

            for s in summaries:
                await self.repo.save_metric_summary(run_id, s)
            await self.repo.update_run(
                run_id, status=status, overall_score=score,
                error_cases=report.error_cases,
                cost_usd=report.total_cost_usd,
                finished_at=report.finished_at,
            )
            if render:
                report.details["reports"] = self._render_reports(config, report)
        except Exception as exc:  # noqa: BLE001 - run-level failure must be persisted
            logger.exception("run failed")
            report.status = "error"
            report.details["error"] = f"{type(exc).__name__}: {exc}"
            await self.repo.update_run(
                run_id, status="error", error=str(exc)[:2000], finished_at=utcnow())
        finally:
            if connector_override is None:
                await target.close()
            if gateway is not None:
                await gateway.close()
        self._emit("run_finished", run_id=run_id, status=report.status)
        self.progress and self.progress.finish(
            f"{config.run_name}: {report.status} "
            f"({report.total_cases} cases, ${report.total_cost_usd:.4f})")
        return report

    run = run_suite  # friendly alias

    # ------------------------------------------------------------------ phase A
    def _build_target(self, config: SuiteConfig):
        cls = resolve_connector(config.target.type)
        return cls(config.target)

    async def _load_cases(self, config: SuiteConfig, limit: int | None,
                          dataset_ids: list[str] | None) -> list[TestCase]:
        cases: list[TestCase] = []
        for dc in config.datasets:
            if dataset_ids and not ({dc.id, dc.path, dc.type} & set(dataset_ids)):
                continue
            loader = resolve_dataset(dc.type)()
            raw: list[TestCase] = [c async for c in loader.load(dc)]
            if limit:
                raw = raw[:limit]
            cases.extend(self._expand_variants(raw, dc))
        if not cases:
            raise RuntimeError("no test cases loaded (check datasets/filters)")
        return cases

    @staticmethod
    def _expand_variants(base_cases: list[TestCase], dc) -> list[TestCase]:
        """Materialize repeats / perturbations as derived cases (ExecutionPlan)."""
        out: list[TestCase] = []
        for case in base_cases:
            out.append(case.model_copy(update={"variant": "base", "variant_of": None}))
            for i in range(1, max(dc.repeats, 1)):
                out.append(case.model_copy(update={
                    "variant": f"repeat-{i}", "variant_of": case.id}))
            for kind in dc.perturbations or []:
                if not isinstance(case.input, str):
                    continue
                perturbed = perturb_text(case.input, kind,
                                         seed=stable_seed(f"{case.id}:{kind}"))
                out.append(case.model_copy(update={
                    "variant": f"perturb-{kind}", "variant_of": case.id,
                    "input": perturbed}))
        return out

    async def _execute_all(self, config: SuiteConfig, target, cases: list[TestCase],
                           run_id: str, report: RunReport) -> list[dict]:
        rl = config.target.rate_limit
        concurrency = config.concurrency or rl.concurrency or 8
        limiter = InProcessRateLimiter(rl.rpm) if rl.rpm else None
        sem = asyncio.Semaphore(max(concurrency, 1))
        options = InvokeOptions(**{k: v for k, v in (config.target.params or {}).items()
                                   if k in InvokeOptions.model_fields})
        options.extra = {k: v for k, v in (config.target.params or {}).items()
                         if k not in InvokeOptions.model_fields}
        if options.stream is None:
            options.stream = config.target.stream

        suite_id = await self.repo.upsert_suite(config.run_name)
        case_row_cache: dict[str, int] = {}
        executed: list[dict] = []
        done = 0
        self.progress and self.progress.start(len(cases), "executing cases")
        self._emit("phase", phase="execute", total=len(cases))

        async def one(case: TestCase) -> dict:
            nonlocal done, report
            async with sem:
                if limiter:
                    await limiter.acquire()
                if config.max_cost_usd is not None and report.total_cost_usd >= config.max_cost_usd:
                    response = TargetResponse(error="cost budget exceeded (circuit breaker)")
                else:
                    try:
                        response = await with_timeout(
                            target.invoke(case, options), config.case_timeout_s)
                    except Exception as exc:  # noqa: BLE001
                        response = TargetResponse(error=f"{type(exc).__name__}: {exc}")
            if response.cost_usd:
                report.total_cost_usd = round(report.total_cost_usd + response.cost_usd, 8)
            if not response.ok:
                report.error_cases += 1
            if case.id not in case_row_cache:
                case_row_cache[case.id] = await self.repo.save_test_case(suite_id, case)
            result_row = await self.repo.save_case_result(
                run_id, case_row_cache[case.id], case.id, response,
                dataset=case.dataset, variant=case.variant, variant_of=case.variant_of)
            done += 1
            self.progress and self.progress.update(1, note=f"{case.id}:{case.variant}")
            if done % 5 == 0 or done == len(cases):
                self._emit("progress", phase="execute", done=done, total=len(cases))
            return {"case": case, "response": response, "result_row": result_row}

        executed = list(await asyncio.gather(*[one(c) for c in cases]))
        self._emit("phase", phase="execute_done", done=len(executed))
        return executed

    # ------------------------------------------------------------------ phase B
    def _metric_infos(self, config: SuiteConfig) -> dict[str, tuple[Any, Any, float | None]]:
        infos: dict[str, tuple[Any, Any, float | None]] = {}
        for m in config.metrics:
            infos[m.name] = (resolve_metric(m.name)(threshold=m.threshold, **m.params),
                             None, m.threshold)
        for j in config.judges:
            judge = self._build_judge(j)
            # judge thresholds live on their raw scale; the aggregator sees the
            # normalized equivalent (threshold / scale_max)
            infos[judge.name] = (None, judge, judge.normalized_threshold)
        return infos

    def _build_judge(self, jc):
        cls = resolve_judge(jc.type)
        params: dict[str, Any] = dict(jc.params)
        if jc.rubric:
            params.setdefault("rubric", jc.rubric)
        model_alias = None
        if jc.type == "multi":
            params.setdefault("models", [
                (m.name or m.model) for m in jc.models
            ])
        elif jc.model is not None:
            model_alias = jc.model.name or jc.model.model
            params.setdefault("model", model_alias)
        return cls(threshold=jc.threshold, weight=jc.weight, **params)

    async def _evaluate(self, config: SuiteConfig, executed: list[dict], ctx,
                        report: RunReport) -> list[CaseMetricRecord]:
        metrics: list[tuple[str, Any]] = []
        for m in config.metrics:
            metrics.append((m.name, resolve_metric(m.name)(threshold=m.threshold, **m.params)))
        judges: list[tuple[str, Any]] = []
        for jc in config.judges:
            judges.append((jc.type, self._build_judge(jc)))

        groups = self._variant_groups(executed)
        base_entries = [e for e in executed if e["case"].variant == "base"]
        total_work = len(base_entries) * (len(metrics) + len(judges))
        done = 0
        cost_tripped = False
        self.progress and self.progress.start(total_work, "computing metrics")
        self._emit("phase", phase="evaluate", total=total_work)

        records: list[CaseMetricRecord] = []
        group_by_map = {dc.label: dc.group_by for dc in config.datasets}

        async def eval_one(entry: dict) -> None:
            nonlocal done, cost_tripped
            case: TestCase = entry["case"]
            response: TargetResponse = entry["response"]
            variants = groups.get(case.id, [])
            dataset = case.dataset
            group_key = group_by_map.get(dataset)
            group = str(case.metadata.get(group_key)) if group_key and group_key in case.metadata else None
            results: list[MetricResult] = []

            for name, metric in metrics:
                try:
                    if metric.wants_variants:
                        res = await metric.compute(case=case, response=response,
                                                   responses=variants, ctx=ctx)
                    else:
                        res = await metric.compute(case=case, response=response, ctx=ctx)
                    if res.metric != name:
                        res.metric = name  # keep requested name stable
                except Exception as exc:  # noqa: BLE001
                    res = MetricResult(metric=name, value=0.0,
                                       error=f"metric error: {type(exc).__name__}: {exc}")
                results.append(res)
            await self.repo.save_metric_results(report.run_id, entry["result_row"],
                                                results, source="metric")
            for res in results:
                records.append(CaseMetricRecord(
                    case_id=case.id, metric=res.metric, value=res.value,
                    passed=res.passed, raw=res.raw, error=res.error,
                    dataset=dataset, group=group))

            if judges and not cost_tripped:
                jresults: list[MetricResult] = []
                for jtype, judge in judges:
                    if cost_tripped:
                        break
                    try:
                        res = await judge.judge(case=case, response=response, ctx=ctx)
                    except CostLimitExceeded as exc:
                        cost_tripped = True
                        report.details["cost_breaker"] = str(exc)
                        res = MetricResult(metric=judge.name, value=0.0, passed=False,
                                           error=f"cost breaker: {exc}")
                    except Exception as exc:  # noqa: BLE001
                        res = MetricResult(metric=judge.name, value=0.0,
                                           error=f"judge error: {type(exc).__name__}: {exc}")
                    if res.metric != jtype:
                        res.metric = jtype
                    jresults.append(res)
                await self.repo.save_metric_results(report.run_id, entry["result_row"],
                                                    jresults, source="judge")
                for res in jresults:
                    records.append(CaseMetricRecord(
                        case_id=case.id, metric=res.metric, value=res.value,
                        passed=res.passed, raw=res.raw, error=res.error,
                        dataset=dataset, group=group))

            done += len(metrics) + len(judges)
            self.progress and self.progress.update(len(metrics) + len(judges))
            if done % 10 == 0 or done >= total_work:
                self._emit("progress", phase="evaluate", done=done, total=total_work)

        sem = asyncio.Semaphore(max(config.concurrency or 8, 1))
        eval_coros = []
        for entry in base_entries:
            async def bounded(e=entry):
                async with sem:
                    await eval_one(e)
            eval_coros.append(bounded())
        await asyncio.gather(*eval_coros)
        return records

    @staticmethod
    def _variant_groups(executed: list[dict]) -> dict[str, list[tuple[TestCase, TargetResponse]]]:
        groups: dict[str, list[tuple[TestCase, TargetResponse]]] = {}
        for e in executed:
            case = e["case"]
            key = case.variant_of or case.id
            groups.setdefault(key, []).append((case, e["response"]))
        # deterministic order: base first, then repeat-N, perturb-*
        def _order(pair):
            v = pair[0].variant
            if v == "base":
                return (0, "")
            if v.startswith("repeat-"):
                return (1, v)
            return (2, v)
        for key in groups:
            groups[key].sort(key=_order)
        return groups

    # ------------------------------------------------------------------ reports
    def _render_reports(self, config: SuiteConfig, report: RunReport) -> dict[str, str]:
        out_dir = Path(config.output_dir) / report.run_name / (report.run_id or "latest")[:8]
        out_dir.mkdir(parents=True, exist_ok=True)
        written: dict[str, str] = {}
        for fmt in config.report:
            try:
                renderer = resolve_renderer(fmt)()
                path = renderer.render(report, out_dir)
                written[fmt] = str(path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("renderer %s failed: %s", fmt, exc)
                written[fmt] = f"error: {exc}"
        return written

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self.event_cb is None:
            return
        try:
            self.event_cb({"type": event_type,
                           "ts": time.time(), **payload})
        except Exception:  # noqa: BLE001 - events must never break the run
            logger.debug("event callback failed", exc_info=True)


class TestRunner(ExecutionEngine):
    """Public API entrypoint (thin name kept for API stability)."""


def build_repository(storage_url: str) -> Repository:
    from aite.storage.db import Database
    from aite.storage.repositories import SqlAlchemyRepository

    return SqlAlchemyRepository(Database(storage_url))


async def run_suite_file(path: str | Path, **kwargs: Any) -> RunReport:
    """Convenience: load a suite YAML and run it with default services."""
    from aite.core.loader import load_suite

    config = load_suite(path)
    repo = build_repository(config.storage.url)
    await repo.init()
    try:
        engine = ExecutionEngine(repo)
        return await engine.run_suite(config, **kwargs)
    finally:
        await repo.close()


_ = registry  # exported for advanced users poking component lists
