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
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aite.core.aggregator import aggregate, decide_status, execution_status
from aite.core.context import build_context, build_gateway
from aite.core.registry import (
    load_plugins,
    registry,
    resolve_connector,
    resolve_dataset,
    resolve_judge,
    resolve_metric,
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
from aite.reporting.renderers import report_relpath
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


def _expected_str(expected: Any) -> str:
    """expected 字段快照：标量直取，dict/list 转 JSON（截断交给调用方）。"""
    if isinstance(expected, (dict, list)):
        return json.dumps(expected, ensure_ascii=False)
    return "" if expected is None else str(expected)


def _case_payloads(executed: list[dict]) -> dict[str, dict]:
    """逐 case 的输入/输出/预期快照（报告与 bad-case 分析用，逐字段截断控体积）。"""
    payloads: dict[str, dict] = {}
    for e in executed:
        case: TestCase = e["case"]
        response: TargetResponse = e["response"]
        if case.id in payloads:
            continue  # 变体与 base 同 id，保留首条
        payloads[case.id] = {
            "input": str(case.input)[:600],
            "output": (response.output_text or "")[:800],
            "expected": _expected_str(case.expected)[:400],
            "target_error": (response.error or "")[:300],
        }
    return payloads


def _log_metric_result(metric: str, case_id: str, res: MetricResult, *,
                       judge: str = "") -> None:
    """逐判定过程日志：干什么、结果值或错误——评测过程的可观测性基线。"""
    who = f"judge[{judge}] " if judge else ""
    if res.error:
        logger.warning("metric %s%s on %s errored: %s", who, metric, case_id,
                       (res.error or "").splitlines()[0])
    else:
        logger.info("metric %s%s on %s = %s", who, metric, case_id, res.value)


def _log_run_header(config: SuiteConfig, cases: list[TestCase]) -> None:
    """运行头：target/数据集/指标/判官/并发一屏可见——评测异常时的第一排查入口。"""
    ds_counts: dict[str, int] = {}
    for c in cases:
        key = c.dataset or "-"
        ds_counts[key] = ds_counts.get(key, 0) + 1
    metrics = ", ".join(m.name + (f">={m.threshold}" if m.threshold is not None else "")
                        for m in config.metrics) or "-"
    judges = ", ".join(
        (j.model.name or j.model.model or j.type) if j.model is not None
        else "/".join(m.name or m.model or "" for m in j.models) or j.type
        for j in config.judges) or "-"
    rl = config.target.rate_limit
    logger.info(
        "run %s | target %s@%s | cases %d [%s] | metrics [%s] | judges [%s] | concurrency %s",
        config.run_name, config.target.model, config.target.base_url, len(cases),
        ", ".join(f"{k}={v}" for k, v in ds_counts.items()),
        metrics, judges,
        config.concurrency or rl.concurrency or "default(8)",
    )


def _log_run_summary(report: RunReport, records: list[CaseMetricRecord]) -> None:
    """收尾摘要 + 指标判定错误统计（error 非空的判定会污染均值/置 0，判读前必看）。"""
    err_by_metric: dict[str, int] = {}
    for rec in records:
        if rec.error:
            err_by_metric[rec.metric] = err_by_metric.get(rec.metric, 0) + 1
    logger.info("run %s finished: status=%s cases=%d target_errors=%d cost=$%.4f",
                report.run_name, report.status, report.total_cases,
                report.error_cases, report.total_cost_usd)
    if err_by_metric:
        logger.warning("metric computations errored (相关均值不可信，详见 report.json "
                       "case_metrics[].error): %s",
                       ", ".join(f"{k}x{v}" for k, v in sorted(err_by_metric.items())))


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
        run_cb: Any = None,
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
        if run_cb:
            await run_cb(run_id)

        gateway = build_gateway(config)
        ctx = build_context(config, gateway, run_id)
        target = connector_override or self._build_target(config)

        try:
            # 解析全部 metrics/judges 必须发生在执行用例之前：
            # 指标名写错时在花掉 API 成本前就失败（fail fast），而不是 Phase B 才报错
            self._metric_infos(config)
            cases = await self._load_cases(config, limit, dataset_ids)
            report.total_cases = len({c.id for c in cases})
            await self.repo.update_run(run_id, total_cases=report.total_cases)
            _log_run_header(config, cases)

            # ---------------- Phase A: execute ----------------
            executed = await self._execute_all(config, target, cases, run_id, report)

            # ---------------- Phase B: evaluate ----------------
            records = await self._evaluate(config, executed, ctx, report)

            summaries = aggregate(records, self._metric_infos(config))
            report.summaries = summaries
            # 指标判定全挂（每条记录 value=None 且带 error，如 swe harness 起不来/
            # 判官前指标全错）：聚合为空 ≠ 通过——decide_status 对空集返回 passed
            # 是「未配阈值跑完即过」的口径，会把打分基建故障包装成「已成功」；
            # 此处覆写为 run 级 error（宁缺毋错），任务层凭 status=error 落 failed
            metric_failed = bool(records) and not summaries \
                and all(r.error for r in records)
            if metric_failed:
                first_err = next(r for r in records if r.error).error or "unknown"
                report.details["error"] = f"metric evaluation failed: {first_err}"
            status, score = decide_status(summaries, config.pass_strategy, config.weights)
            if metric_failed:
                status, score = "error", None
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
                # 指标全挂的 error 文案随 run 行落库（前端报错卡的唯一来源）
                error=report.details.get("error") if metric_failed else None,
            )
            if render:
                report.details["reports"] = self._render_reports(config, report)
            _log_run_summary(report, records)
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
            f"{config.run_name} · 执行 {execution_status(report)} "
            f"({report.total_cases - report.error_cases}/{report.total_cases} case 调用成功) · "
            f"评估结论 {report.status} "
            f"(cost ${report.total_cost_usd:.4f})")
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
            if dc.ref:      # catalog 引用（§12.4）→ 解析为本地文件数据集
                from aite.datasets.catalog import resolve_dataset_ref

                row = await resolve_dataset_ref(self.repo, dc.ref)
                dc = dc.model_copy(update={"type": "local", "path": row["path"],
                                           "id": row["name"], "ref": None})
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
        failures: list[dict] = []
        done = 0
        self.progress and self.progress.start(len(cases), "executing cases")
        self._emit("phase", phase="execute", total=len(cases))

        async def one(case: TestCase) -> dict:
            nonlocal done, report
            async with sem:
                if limiter:
                    await limiter.acquire()
                # 软预算：熔断读与成本累加之间隔着 invoke await，在途请求成本不可见，
                # 实际花费可超出 max_cost_usd 最多「并发数 × 单请求成本」——固有近似，
                # 累加本身均在无 await 的同步段（事件循环内原子），无丢更新竞态
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
                # 执行失败明细进报告（CLI 汇总打印）：target 挂掉时指标全 0，
                # 用户必须能直接看到「哪条 case、什么错」，而不是自己翻库
                failures.append({
                    "dataset": case.dataset or "",
                    "case": f"{case.id}:{case.variant}",
                    "detail": (response.error or "")[:300],
                })
            if case.id not in case_row_cache:
                case_row_cache[case.id] = await self.repo.save_test_case(suite_id, case)
            result_row = await self.repo.save_case_result(
                run_id, case_row_cache[case.id], case.id, response,
                dataset=case.dataset, variant=case.variant, variant_of=case.variant_of)
            done += 1
            self.progress and self.progress.update(1, note=f"{case.id}:{case.variant}")
            if response.ok:
                logger.info("case %s:%s ok %.0fms", case.id, case.variant,
                            response.timings.total_ms or 0)
            else:
                logger.warning("case %s:%s FAILED: %s", case.id, case.variant,
                               (response.error or "").splitlines()[0])
            if done % 5 == 0 or done == len(cases):
                self._emit("progress", phase="execute", done=done, total=len(cases))
            return {"case": case, "response": response, "result_row": result_row}

        executed = list(await asyncio.gather(*[one(c) for c in cases]))
        report.details["case_payloads"] = _case_payloads(executed)
        if failures:
            report.details["failures"] = failures[:50]
        self._emit("phase", phase="execute_done", done=len(executed))
        return executed

    def _progress_note(self, note: str) -> None:
        """进度条备注：当前正在算的指标/判官与用例（不推进进度）。"""
        self.progress and self.progress.update(0, note=note[:60])

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
        """阶段 B：指标计算 + judge 评估（并发，带成本熔断）。"""
        infos = self._metric_infos(config)
        metrics = [(name, m) for name, (m, _j, _t) in infos.items() if m is not None]
        # judge 以 suite 配置的 type 名入库（历史口径），与 judge.name 可能不同
        judges = [(jc.type, self._build_judge(jc)) for jc in config.judges]
        groups = self._variant_groups(executed)
        base_entries = [e for e in executed if e["case"].variant == "base"]
        group_by_map = {dc.label: dc.group_by for dc in config.datasets}
        total_work = len(base_entries) * (len(metrics) + len(judges))
        done = 0
        # cost_tripped 跨任务单向标志：事件循环内同步赋值，无竞态
        state = {"cost_tripped": False}
        records: list[CaseMetricRecord] = []
        self.progress and self.progress.start(total_work, "computing metrics")
        self._emit("phase", phase="evaluate", total=total_work)

        sem = asyncio.Semaphore(max(config.concurrency or 8, 1))

        async def eval_one(entry: dict) -> None:
            nonlocal done
            async with sem:
                await self._eval_entry(entry, metrics, judges, groups, group_by_map,
                                       ctx, report, records, state)
            done += len(metrics) + len(judges)
            self.progress and self.progress.update(len(metrics) + len(judges))
            if done % 10 == 0 or done >= total_work:
                self._emit("progress", phase="evaluate", done=done, total=total_work)

        await asyncio.gather(*(eval_one(e) for e in base_entries))
        return records

    async def _eval_entry(self, entry: dict, metrics, judges, groups, group_by_map,
                          ctx, report: RunReport, records: list,
                          state: dict) -> None:
        """单用例求值：指标计算 → 持久化 → judge（成本熔断）→ 持久化。"""
        case: TestCase = entry["case"]
        response: TargetResponse = entry["response"]
        variants = groups.get(case.id, [])
        dataset = case.dataset
        group_key = group_by_map.get(dataset)
        group = str(case.metadata.get(group_key)) if group_key and group_key in case.metadata else None

        results: list[MetricResult] = []
        for name, metric in metrics:
            self._progress_note(f"{name} ← {case.id}")
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
            _log_metric_result(name, case.id, res)
            results.append(res)
        await self.repo.save_metric_results(report.run_id, entry["result_row"],
                                            results, source="metric")
        records.extend(CaseMetricRecord(
            case_id=case.id, metric=res.metric, value=res.value,
            passed=res.passed, raw=res.raw, error=res.error,
            dataset=dataset, group=group) for res in results)

        if judges and not state["cost_tripped"]:
            jresults = await self._run_judges(judges, case, response, ctx, report, state)
            await self.repo.save_metric_results(report.run_id, entry["result_row"],
                                                jresults, source="judge")
            records.extend(CaseMetricRecord(
                case_id=case.id, metric=res.metric, value=res.value,
                passed=res.passed, raw=res.raw, error=res.error,
                dataset=dataset, group=group) for res in jresults)

    async def _run_judges(self, judges, case: TestCase, response: TargetResponse,
                          ctx, report: RunReport, state: dict) -> list[MetricResult]:
        """judge 评估；CostLimitExceeded 触发全局熔断（后续用例跳过 judge）。"""
        jresults: list[MetricResult] = []
        for jtype, judge in judges:
            if state["cost_tripped"]:
                break
            alias = judge.params.get("model") or jtype
            self._progress_note(f"judge {alias} ← {case.id}")
            try:
                res = await judge.judge(case=case, response=response, ctx=ctx)
            except CostLimitExceeded as exc:
                state["cost_tripped"] = True
                report.details["cost_breaker"] = str(exc)
                res = MetricResult(metric=judge.name, value=0.0, passed=False,
                                   error=f"cost breaker: {exc}")
            except Exception as exc:  # noqa: BLE001
                res = MetricResult(metric=judge.name, value=0.0,
                                   error=f"judge error: {type(exc).__name__}: {exc}")
            if res.metric != jtype:
                res.metric = jtype
            _log_metric_result(jtype, case.id, res, judge=alias)
            jresults.append(res)
        return jresults

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
        from aite.reporting.renderers import render_report_formats

        return render_report_formats(
            report, Path(config.output_dir) / report_relpath(report), config.report)

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
