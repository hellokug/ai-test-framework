"""Repository implementation over async SQLAlchemy (works for SQLite/MySQL/PG)."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from aite.core.interfaces import Repository
from aite.core.types import MetricResult, TargetResponse, TestCase, utcnow
from aite.storage.db import Database
from aite.storage.models import (
    BgTaskRow,
    ManualEvalScoreRow,
    ManualEvalTaskRow,
    MetricResultRow,
    ModelConfigRow,
    RunMetricSummaryRow,
    TestCaseResultRow,
    TestCaseRow,
    TestRunRow,
    TestSuiteRow,
    aware,
    new_id,
)

logger = logging.getLogger("aite.storage.repos")


def _json_safe(value: Any) -> Any:
    """Best-effort JSON-serializable conversion for JSON columns."""
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


class SqlAlchemyRepository(Repository):
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------------------------------------------------------------- lifecycle
    async def init(self) -> None:
        await self.db.init()

    async def close(self) -> None:
        await self.db.dispose()

    # ---------------------------------------------------------------- suites
    async def upsert_suite(self, name: str, config: dict[str, Any] | None = None) -> str:
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(TestSuiteRow).where(TestSuiteRow.name == name)
            )).scalar_one_or_none()
            if row is None:
                row = TestSuiteRow(id=new_id(), name=name)
                session.add(row)
            if config:
                row.config = _json_safe(config)
            await session.commit()
            return str(row.id)

    async def get_suite_by_name(self, name: str) -> dict | None:
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(TestSuiteRow).where(TestSuiteRow.name == name)
            )).scalar_one_or_none()
            return self._suite_dict(row) if row else None

    async def list_suites(self) -> list[dict]:
        async with self.db.sessions() as session:
            rows = (await session.execute(
                select(TestSuiteRow).order_by(TestSuiteRow.updated_at.desc())
            )).scalars().all()
            return [self._suite_dict(r) for r in rows]

    @staticmethod
    def _suite_dict(row: TestSuiteRow) -> dict:
        return {
            "id": str(row.id), "name": row.name, "description": row.description,
            "config": row.config or {}, "updated_at": aware(row.updated_at).isoformat(),
        }

    # ---------------------------------------------------------------- cases
    async def save_test_case(self, suite_id: str, case: TestCase) -> int:
        async with self.db.sessions() as session:
            external_key = f"{case.id}:{case.variant}"
            row = (await session.execute(
                select(TestCaseRow).where(
                    TestCaseRow.suite_id == suite_id, TestCaseRow.external_key == external_key
                )
            )).scalar_one_or_none()
            if row is None:
                row = TestCaseRow(suite_id=suite_id, external_key=external_key)
                session.add(row)
            row.input = _json_safe(case.input)
            row.expected = _json_safe(case.expected)
            row.metadata_ = _json_safe(case.metadata or {})
            row.retrieval_context = _json_safe(case.retrieval_context)
            row.variant, row.variant_of = case.variant, case.variant_of
            row.dataset = case.dataset
            await session.commit()
            return int(row.id)

    async def list_test_cases(self, suite_id: str, limit: int = 200) -> list[dict]:
        async with self.db.sessions() as session:
            rows = (await session.execute(
                select(TestCaseRow).where(TestCaseRow.suite_id == suite_id)
                .order_by(TestCaseRow.id).limit(limit)
            )).scalars().all()
            return [self._case_dict(r) for r in rows]

    async def get_case(self, case_row_id: int) -> dict | None:
        async with self.db.sessions() as session:
            row = await session.get(TestCaseRow, case_row_id)
            return self._case_dict(row) if row else None

    @staticmethod
    def _case_dict(row: TestCaseRow) -> dict:
        return {
            "id": row.id, "external_key": row.external_key, "input": row.input,
            "expected": row.expected, "metadata": row.metadata_ or {},
            "retrieval_context": row.retrieval_context, "variant": row.variant,
            "variant_of": row.variant_of, "dataset": row.dataset,
        }

    # ---------------------------------------------------------------- runs
    async def create_run(self, run_name: str, config_snapshot: dict,
                         suite_id: str | None = None) -> str:
        run_id = new_id()
        async with self.db.sessions() as session:
            session.add(TestRunRow(
                id=run_id, run_name=run_name, suite_id=suite_id,
                config_snapshot=_json_safe(config_snapshot),
            ))
            await session.commit()
        return run_id

    async def update_run(self, run_id: str, **fields: Any) -> None:
        allowed = {"status", "overall_score", "total_cases", "error_cases", "cost_usd",
                   "finished_at", "error"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        if isinstance(updates.get("finished_at"), str):
            updates["finished_at"] = updates["finished_at"]
        async with self.db.sessions() as session:
            row = await session.get(TestRunRow, run_id)
            if row is None:
                return
            for k, v in updates.items():
                setattr(row, k, v)
            await session.commit()

    async def get_run(self, run_id: str) -> dict | None:
        async with self.db.sessions() as session:
            row = await session.get(TestRunRow, run_id)
            return self._run_dict(row) if row else None

    async def list_runs(self, limit: int = 50, offset: int = 0,
                        run_name: str | None = None) -> list[dict]:
        async with self.db.sessions() as session:
            q = select(TestRunRow).order_by(TestRunRow.started_at.desc())
            if run_name:
                q = q.where(TestRunRow.run_name == run_name)
            rows = (await session.execute(q.limit(limit).offset(offset))).scalars().all()
            return [self._run_dict(r) for r in rows]

    @staticmethod
    def _run_dict(row: TestRunRow) -> dict:
        return {
            "id": str(row.id), "run_name": row.run_name, "status": row.status,
            "overall_score": row.overall_score, "config": row.config_snapshot or {},
            "total_cases": row.total_cases, "error_cases": row.error_cases,
            "cost_usd": row.cost_usd, "error": row.error,
            "started_at": aware(row.started_at).isoformat() if row.started_at else None,
            "finished_at": aware(row.finished_at).isoformat() if row.finished_at else None,
        }

    # ---------------------------------------------------------------- case results
    async def save_case_result(self, run_id: str, case_row_id: int, case_id: str,
                               response: TargetResponse, dataset: str | None = None,
                               variant: str = "base", variant_of: str | None = None) -> int:
        async with self.db.sessions() as session:
            row = TestCaseResultRow(
                run_id=run_id, case_id=case_row_id, external_key=case_id, dataset=dataset,
                output=_json_safe(response.output), error=response.error,
                usage=response.usage.model_dump() if response.usage else None,
                timings=response.timings.model_dump() if response.timings else None,
                cost_usd=response.cost_usd, variant=variant, variant_of=variant_of,
                trace=response.trace.model_dump() if response.trace else None,
                raw=_json_safe(response.raw),
            )
            session.add(row)
            await session.commit()
            return int(row.id)

    async def list_case_results(self, run_id: str, limit: int | None = None) -> list[dict]:
        async with self.db.sessions() as session:
            q = (select(TestCaseResultRow).where(TestCaseResultRow.run_id == run_id)
                 .order_by(TestCaseResultRow.id))
            if limit:
                q = q.limit(limit)
            rows = (await session.execute(q)).scalars().all()
            return [self._result_dict(r) for r in rows]

    @staticmethod
    def _result_dict(row: TestCaseResultRow) -> dict:
        return {
            "id": row.id, "case_id": row.case_id, "external_key": row.external_key,
            "dataset": row.dataset, "output": row.output, "error": row.error,
            "usage": row.usage, "timings": row.timings, "cost_usd": row.cost_usd,
            "variant": row.variant, "variant_of": row.variant_of, "trace": row.trace,
        }

    # ---------------------------------------------------------------- metrics
    async def save_metric_results(self, run_id: str, case_result_id: int,
                                  results: list[MetricResult], source: str = "metric") -> None:
        async with self.db.sessions() as session:
            for res in results:
                session.add(MetricResultRow(
                    run_id=run_id, case_result_id=case_result_id, metric=res.metric,
                    value=res.value, passed=res.passed, raw=_json_safe(res.raw),
                    error=res.error, source=source,
                ))
            await session.commit()

    async def list_metric_results(self, run_id: str, metric: str | None = None) -> list[dict]:
        async with self.db.sessions() as session:
            q = select(MetricResultRow).where(MetricResultRow.run_id == run_id)
            if metric:
                q = q.where(MetricResultRow.metric == metric)
            rows = (await session.execute(q.order_by(MetricResultRow.id))).scalars().all()
            return [{
                "id": r.id, "case_result_id": r.case_result_id, "metric": r.metric,
                "value": r.value, "passed": r.passed, "raw": r.raw, "error": r.error,
                "source": r.source,
            } for r in rows]

    async def save_metric_summary(self, run_id: str, summary: Any) -> None:
        async with self.db.sessions() as session:
            run = await session.get(TestRunRow, run_id)
            run_name = run.run_name if run else ""
            session.add(RunMetricSummaryRow(
                run_id=run_id, run_name=run_name, metric=summary.metric,
                dataset=summary.dataset, group=summary.group,
                aggregation=summary.aggregation, value=summary.value,
                threshold=summary.threshold, passed=summary.passed,
                count=summary.count, pass_rate=summary.pass_rate,
            ))
            await session.commit()

    async def list_metric_summaries(self, run_id: str) -> list[dict]:
        async with self.db.sessions() as session:
            rows = (await session.execute(
                select(RunMetricSummaryRow).where(RunMetricSummaryRow.run_id == run_id)
                .order_by(RunMetricSummaryRow.id)
            )).scalars().all()
            return [{
                "metric": r.metric, "dataset": r.dataset, "group": r.group,
                "aggregation": r.aggregation, "value": r.value, "threshold": r.threshold,
                "passed": r.passed, "count": r.count, "pass_rate": r.pass_rate,
            } for r in rows]

    async def trends(self, run_name: str | None = None, metric: str | None = None,
                     days: int = 30) -> list[dict]:
        """Trend query only scans run_metric_summary (denormalized)."""
        async with self.db.sessions() as session:
            since = utcnow() - timedelta(days=days)
            q = select(RunMetricSummaryRow).where(
                RunMetricSummaryRow.dataset.is_(None), RunMetricSummaryRow.group.is_(None),
                RunMetricSummaryRow.created_at >= since,
            )
            if run_name:
                q = q.where(RunMetricSummaryRow.run_name == run_name)
            if metric:
                q = q.where(RunMetricSummaryRow.metric == metric)
            rows = (await session.execute(
                q.order_by(RunMetricSummaryRow.created_at)
            )).scalars().all()
            return [{
                "run_id": str(r.run_id), "run_name": r.run_name, "metric": r.metric,
                "value": r.value, "threshold": r.threshold, "passed": r.passed,
                "created_at": aware(r.created_at).isoformat(),
            } for r in rows]

    # ---------------------------------------------------------------- model configs
    async def save_model_config(self, data: dict[str, Any]) -> str:
        async with self.db.sessions() as session:
            name = data.get("name") or data.get("model") or new_id()
            row = (await session.execute(
                select(ModelConfigRow).where(ModelConfigRow.name == name)
            )).scalar_one_or_none()
            if row is None:
                row = ModelConfigRow(id=new_id(), name=name)
                session.add(row)
            for field in ("type", "base_url", "model", "api_key_env", "notes"):
                if data.get(field) is not None:
                    setattr(row, field, data[field])
            if data.get("params"):
                row.params = _json_safe(data["params"])
            await session.commit()
            return str(row.id)

    async def list_model_configs(self) -> list[dict]:
        async with self.db.sessions() as session:
            rows = (await session.execute(
                select(ModelConfigRow).order_by(ModelConfigRow.name)
            )).scalars().all()
            return [{
                "id": str(r.id), "name": r.name, "type": r.type, "base_url": r.base_url,
                "model": r.model, "api_key_env": r.api_key_env, "params": r.params,
                "notes": r.notes,
            } for r in rows]

    async def delete_model_config(self, model_id: str) -> bool:
        async with self.db.sessions() as session:
            row = await session.get(ModelConfigRow, model_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    # ---------------------------------------------------------------- manual eval
    async def create_manual_task(self, data: dict[str, Any]) -> str:
        task_id = new_id()
        async with self.db.sessions() as session:
            session.add(ManualEvalTaskRow(
                id=task_id, run_id=data.get("run_id"), case_result_id=data.get("case_result_id"),
                title=data.get("title", ""), case_input=_json_safe(data.get("case_input")),
                expected=_json_safe(data.get("expected")), output=_json_safe(data.get("output")),
                rubric=data.get("rubric"),
            ))
            await session.commit()
        return task_id

    async def list_manual_tasks(self, status: str | None = None, limit: int = 100) -> list[dict]:
        async with self.db.sessions() as session:
            q = select(ManualEvalTaskRow).order_by(ManualEvalTaskRow.created_at.desc())
            if status:
                q = q.where(ManualEvalTaskRow.status == status)
            rows = (await session.execute(q.limit(limit))).scalars().all()
            out = []
            for r in rows:
                scores = (await session.execute(
                    select(ManualEvalScoreRow).where(ManualEvalScoreRow.task_id == r.id)
                )).scalars().all()
                out.append({
                    "id": str(r.id), "run_id": str(r.run_id) if r.run_id else None,
                    "case_result_id": r.case_result_id, "title": r.title,
                    "case_input": r.case_input, "expected": r.expected, "output": r.output,
                    "rubric": r.rubric, "status": r.status, "assignee": r.assignee,
                    "scores": [{
                        "score": s.score, "passed": s.passed, "reviewer": s.reviewer,
                        "comment": s.comment, "created_at": aware(s.created_at).isoformat(),
                    } for s in scores],
                })
            return out

    async def submit_manual_score(self, task_id: str, score: float, reviewer: str = "",
                                  comment: str | None = None, passed: bool | None = None) -> bool:
        async with self.db.sessions() as session:
            task = await session.get(ManualEvalTaskRow, task_id)
            if task is None:
                return False
            session.add(ManualEvalScoreRow(
                id=new_id(), task_id=task_id, score=score, passed=passed,
                reviewer=reviewer, comment=comment,
            ))
            task.status = "done"
            task.assignee = reviewer or task.assignee
            await session.commit()
            return True

    async def export_manual_scores(self, run_id: str | None = None) -> list[dict]:
        async with self.db.sessions() as session:
            q = (select(ManualEvalScoreRow, ManualEvalTaskRow)
                 .join(ManualEvalTaskRow, ManualEvalScoreRow.task_id == ManualEvalTaskRow.id))
            if run_id:
                q = q.where(ManualEvalTaskRow.run_id == run_id)
            rows = (await session.execute(q)).all()
            return [{
                "task_id": str(task.id), "title": task.title, "run_id": str(task.run_id) if task.run_id else None,
                "case_input": task.case_input, "expected": task.expected, "output": task.output,
                "score": score.score, "passed": score.passed, "reviewer": score.reviewer,
                "comment": score.comment, "created_at": aware(score.created_at).isoformat(),
            } for score, task in rows]

    # ---------------------------------------------------------------- bg tasks
    async def create_bg_task(self, kind: str, payload: dict[str, Any]) -> str:
        task_id = new_id()
        async with self.db.sessions() as session:
            session.add(BgTaskRow(id=task_id, kind=kind, payload=_json_safe(payload)))
            await session.commit()
        return task_id

    async def update_bg_task(self, task_id: str, status: str | None = None,
                             result: dict | None = None, error: str | None = None,
                             run_id: str | None = None) -> None:
        async with self.db.sessions() as session:
            row = await session.get(BgTaskRow, task_id)
            if row is None:
                return
            if status:
                row.status = status
            if result is not None:
                row.result = _json_safe(result)
            if error is not None:
                row.error = error
            if run_id is not None:
                row.run_id = run_id
            if status in ("done", "error"):
                row.finished_at = utcnow()
            await session.commit()

    async def list_bg_tasks(self, limit: int = 50) -> list[dict]:
        async with self.db.sessions() as session:
            rows = (await session.execute(
                select(BgTaskRow).order_by(BgTaskRow.created_at.desc()).limit(limit)
            )).scalars().all()
            return [{
                "id": str(r.id), "kind": r.kind, "status": r.status, "payload": r.payload,
                "result": r.result, "error": r.error, "run_id": str(r.run_id) if r.run_id else None,
                "created_at": aware(r.created_at).isoformat(),
                "finished_at": aware(r.finished_at).isoformat() if r.finished_at else None,
            } for r in rows]

    async def get_bg_task(self, task_id: str) -> dict | None:
        async with self.db.sessions() as session:
            row = await session.get(BgTaskRow, task_id)
            if row is None:
                return None
            return {
                "id": str(row.id), "kind": row.kind, "status": row.status,
                "payload": row.payload, "result": row.result, "error": row.error,
                "run_id": str(row.run_id) if row.run_id else None,
                "created_at": aware(row.created_at).isoformat(),
                "finished_at": aware(row.finished_at).isoformat() if row.finished_at else None,
            }
