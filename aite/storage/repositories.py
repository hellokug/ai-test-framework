"""Repository implementation over async SQLAlchemy (works for SQLite/MySQL/PG)."""

from __future__ import annotations

import json
import logging
from collections.abc import Collection
from datetime import timedelta
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

from aite.core.interfaces import Repository
from aite.core.types import (
    TASK_RUNNING,
    TASK_STALE,
    TASK_STOPPING,
    TASK_TERMINAL,
    MetricResult,
    TargetResponse,
    TestCase,
    utcnow,
)
from aite.storage.db import Database
from aite.storage.models import (
    AgentNodeRow,
    AgentTaskRow,
    BgTaskRow,
    CaseRunRow,
    DatasetCatalogRow,
    DimTagRow,
    ManualEvalScoreRow,
    ManualEvalTaskRow,
    MetricResultRow,
    ModelConfigRow,
    ModelVersionRow,
    RunMetricSummaryRow,
    TestCaseDocRow,
    TestCaseResultRow,
    TestCaseRow,
    TestRunRow,
    TestSuiteRow,
    VersionResultRow,
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

    async def delete_suite(self, name: str) -> bool:
        """删除 suite；引用它的 run 解引用（suite_id 置 NULL）保留审计历史。"""
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(TestSuiteRow).where(TestSuiteRow.name == name)
            )).scalar_one_or_none()
            if row is None:
                return False
            await session.execute(
                update(TestRunRow).where(TestRunRow.suite_id == row.id)
                .values(suite_id=None))
            await session.delete(row)
            await session.commit()
            return True

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
                   "finished_at", "error", "details"}
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
            "details": row.details or {},
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
            q = (select(TestCaseResultRow, TestCaseRow)
                 .outerjoin(TestCaseRow, TestCaseResultRow.case_id == TestCaseRow.id)
                 .where(TestCaseResultRow.run_id == run_id)
                 .order_by(TestCaseResultRow.id))
            if limit:
                q = q.limit(limit)
            pairs = (await session.execute(q)).all()
            items = []
            for res, case in pairs:
                item = self._result_dict(res)
                item["input"] = _json_safe(case.input) if case else None
                item["expected"] = _json_safe(case.expected) if case else None
                item["metadata"] = (case.metadata_ or {}) if case else {}   # 用例 id/类型/等级（回填对表）
                items.append(item)
            return items

    @staticmethod
    def _result_dict(row: TestCaseResultRow) -> dict:
        return {
            "id": row.id, "case_id": row.case_id, "external_key": row.external_key,
            "dataset": row.dataset, "output": row.output, "error": row.error,
            "usage": row.usage, "timings": row.timings, "cost_usd": row.cost_usd,
            "variant": row.variant, "variant_of": row.variant_of, "trace": row.trace,
            "raw": _json_safe(row.raw),
        }

    # ------------------------------------------------ tool 用例级落库（§16）
    async def save_tool_case_record(self, run_id: str, suite_id: str,
                                    record: dict[str, Any]) -> int:
        """工具域用例原子落库：test_case 快照（复用按 external_key）→
        case_result（原始输出/错误）→ pass 指标行。单事务。

        行级 dataset 统一为场景名：pytest 适配器只带 ``scenario`` 键，
        显式 ``dataset`` 优先；前端用例明细据此回跳脚本行。"""
        dataset = record.get("dataset") or record.get("scenario")
        async with self.db.sessions() as session:
            case_row = (await session.execute(
                select(TestCaseRow).where(
                    TestCaseRow.suite_id == suite_id,
                    TestCaseRow.external_key == record["external_key"])
            )).scalar_one_or_none()
            if case_row is None:
                case_row = TestCaseRow(
                    suite_id=suite_id, external_key=record["external_key"],
                    input=_json_safe(record.get("messages")),
                    expected=_json_safe(record.get("expect")),
                    metadata_=_json_safe(record.get("meta") or {}),
                    dataset=dataset)
                session.add(case_row)
            else:
                case_row.input = _json_safe(record.get("messages"))
                case_row.expected = _json_safe(record.get("expect"))
                case_row.metadata_ = _json_safe(record.get("meta") or {})
                case_row.dataset = dataset
            await session.flush()
            result_row = TestCaseResultRow(
                run_id=run_id, case_id=int(case_row.id),
                external_key=record["external_key"],
                dataset=dataset,
                output=_json_safe(record.get("response")),
                error=record.get("error"),
                raw=_json_safe({"expect": record.get("expect"),
                                "params": record.get("params"),
                                "tools": record.get("tools")}),
            )
            session.add(result_row)
            await session.flush()
            if record.get("passed") is not None:
                session.add(MetricResultRow(
                    run_id=run_id, case_result_id=int(result_row.id),
                    metric="pass_rate",
                    value=1.0 if record["passed"] else 0.0,
                    passed=record["passed"], source="pytest"))
            await session.commit()
            return int(result_row.id)

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

    # ------------------------------------------------------- model versions（§10.6）
    async def list_model_versions(self) -> list[dict]:
        async with self.db.sessions() as session:
            stmt = select(ModelVersionRow).order_by(
                ModelVersionRow.created_at.desc())
            rows = (await session.execute(stmt)).scalars().all()
            return [{"id": str(r.id), "model": r.model, "version": r.version,
                     "description": r.description,
                     "created_at": aware(r.created_at).isoformat()} for r in rows]

    async def create_model_version(self, data: dict[str, Any]) -> dict:
        """登记版本：版本号唯一（与模型无关），重复显式报错（FileExistsError→409）。"""
        async with self.db.sessions() as session:
            dup = (await session.execute(
                select(ModelVersionRow).where(
                    ModelVersionRow.version == data["version"]),
            )).scalar_one_or_none()
            if dup is not None:
                raise FileExistsError(f"版本 {data['version']} 已登记")
            row = ModelVersionRow(id=new_id(), model=data.get("model", ""),
                                  version=data["version"],
                                  description=data.get("description") or "")
            session.add(row)
            await session.commit()
            return {"id": str(row.id), "model": row.model, "version": row.version,
                    "description": row.description,
                    "created_at": aware(row.created_at).isoformat()}

    async def delete_model_version(self, version_id: str) -> bool:
        async with self.db.sessions() as session:
            row = await session.get(ModelVersionRow, version_id)
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

    async def get_bg_task_by_run(self, run_id: str) -> dict | None:
        """按 run_id 反查最近的后台任务（SSE 频道解析：发布端 channel 用 task id）。"""
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(BgTaskRow).where(BgTaskRow.run_id == run_id)
                .order_by(BgTaskRow.created_at.desc()).limit(1)
            )).scalar_one_or_none()
            return await self.get_bg_task(str(row.id)) if row else None

    async def mark_stale_bg_tasks(self) -> int:
        """启动时清理：崩溃/强杀残留的 created/running 任务标记为 error。

        状态机闭合（CLAUDE.md 原则 8）：进程内句柄消失后 DB 不得停留中间态。
        """
        async with self.db.sessions() as session:
            result = await session.execute(
                update(BgTaskRow)
                .where(BgTaskRow.status.in_(("pending", "running")))
                .values(status="error", error="server restarted before completion",
                        finished_at=utcnow()))
            await session.commit()
            return result.rowcount or 0

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

    # ---------------------------------------------------------------- agents
    async def register_agent(self, name: str, labels: list[str],
                             version: str | None) -> dict:
        """注册执行机；重名接管（同一台机器 agent 重启复用行，id 不变）。"""
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(AgentNodeRow).where(AgentNodeRow.name == name)
            )).scalar_one_or_none()
            if row is None:
                row = AgentNodeRow(id=new_id(), name=name)
                session.add(row)
            row.labels = labels
            row.agent_version = version
            row.status = "online"
            row.last_heartbeat = utcnow()
            await session.commit()
            return {"id": str(row.id), "name": row.name, "labels": row.labels,
                    "status": row.status}

    async def heartbeat_agent(self, agent_id: str, current_task_id: str | None) -> dict | None:
        """心跳：刷新在线状态与当前任务；返回节点信息（None=未注册）。"""
        async with self.db.sessions() as session:
            row = await session.get(AgentNodeRow, agent_id)
            if row is None:
                return None
            row.status = "online"
            row.last_heartbeat = utcnow()
            row.current_task_id = current_task_id
            await session.commit()
            return {"id": str(row.id), "name": row.name, "status": row.status,
                    "current_task_id": current_task_id}

    async def list_agents(self) -> list[dict]:
        async with self.db.sessions() as session:
            rows = (await session.execute(
                select(AgentNodeRow).order_by(AgentNodeRow.name)
            )).scalars().all()
            return [{
                "id": str(r.id), "name": r.name, "labels": r.labels,
                "status": r.status, "agent_version": r.agent_version,
                "current_task_id": str(r.current_task_id) if r.current_task_id else None,
                "last_heartbeat": aware(r.last_heartbeat).isoformat(),
            } for r in rows]

    async def get_agent_by_name(self, name: str) -> dict | None:
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(AgentNodeRow).where(AgentNodeRow.name == name)
            )).scalar_one_or_none()
            return {"id": str(row.id), "name": row.name} if row else None

    async def sweep_stale_agents(self, timeout_s: float) -> int:
        """懒式回收：心跳超时的节点标 offline，其在跑任务标 error（状态机闭合）。"""
        cutoff = utcnow() - timedelta(seconds=timeout_s)
        async with self.db.sessions() as session:
            stale = (await session.execute(
                select(AgentNodeRow).where(
                    AgentNodeRow.status == "online",
                    AgentNodeRow.last_heartbeat < cutoff)
            )).scalars().all()
            if not stale:
                return 0
            stale_ids = [r.id for r in stale]
            for r in stale:
                r.status = "offline"
            tasks = (await session.execute(
                select(AgentTaskRow).where(
                    AgentTaskRow.node_id.in_(stale_ids),
                    AgentTaskRow.status.in_(("dispatched", "running")))
            )).scalars().all()
            for t in tasks:
                t.status = "error"
                t.error = "agent offline (heartbeat timeout)"
                t.finished_at = utcnow()
            await session.commit()
            return len(tasks)

    async def create_agent_task(self, run_name: str, engine: str, suite_raw: dict,
                                node_id: str | None = None) -> dict:
        async with self.db.sessions() as session:
            row = AgentTaskRow(id=new_id(), run_name=run_name, engine=engine,
                               suite_raw=_json_safe(suite_raw), node_id=node_id)
            session.add(row)
            await session.commit()
            return {"id": str(row.id), "status": row.status}

    async def claim_agent_task(self, agent_id: str) -> dict | None:
        """认领：优先该节点被指派的 queued 任务，其次未指派节点的任务。"""
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(AgentTaskRow).where(
                    AgentTaskRow.status == "queued",
                    (AgentTaskRow.node_id == agent_id) | (AgentTaskRow.node_id.is_(None)))
                .order_by(AgentTaskRow.created_at).limit(1)
            )).scalar_one_or_none()
            if row is None:
                return None
            row.status = "dispatched"
            row.dispatched_at = utcnow()
            node = await session.get(AgentNodeRow, agent_id)
            if node is not None:
                node.current_task_id = str(row.id)
            await session.commit()
            return {"id": str(row.id), "run_name": row.run_name,
                    "engine": row.engine, "suite_raw": row.suite_raw}

    async def update_agent_task(self, task_id: str, status: str | None = None,
                                result: dict | None = None,
                                error: str | None = None) -> None:
        async with self.db.sessions() as session:
            row = await session.get(AgentTaskRow, task_id)
            if row is None:
                return
            if status:
                row.status = status
            if result is not None:
                row.result = _json_safe(result)
            if error is not None:
                row.error = error
            if status in ("done", "error", "stale"):
                row.finished_at = utcnow()
            await session.commit()

    async def list_agent_tasks(self, limit: int = 50) -> list[dict]:
        async with self.db.sessions() as session:
            rows = (await session.execute(
                select(AgentTaskRow).order_by(AgentTaskRow.created_at.desc()).limit(limit)
            )).scalars().all()
            return [self._agent_task_dict(r) for r in rows]

    async def get_agent_task(self, task_id: str) -> dict | None:
        async with self.db.sessions() as session:
            row = await session.get(AgentTaskRow, task_id)
            return self._agent_task_dict(row) if row else None

    @staticmethod
    def _agent_task_dict(row: AgentTaskRow) -> dict:
        return {
            "id": str(row.id), "node_id": str(row.node_id) if row.node_id else None,
            "status": row.status, "run_name": row.run_name, "engine": row.engine,
            "suite_raw": row.suite_raw or {},
            "result": row.result, "error": row.error,
            "created_at": aware(row.created_at).isoformat(),
            "dispatched_at": aware(row.dispatched_at).isoformat() if row.dispatched_at else None,
            "finished_at": aware(row.finished_at).isoformat() if row.finished_at else None,
        }

    # ------------------------------------------------- domain tasks（四域任务表）
    # 表类由调用方传入（FunctionalTaskRow / AccuracyTaskRow / PerfTaskRow /
    # ReliabilityTaskRow，四表同构见 models.TaskLifecycleMixin），一份实现服务四域。
    async def create_domain_task(self, table: type[Any], spec: dict[str, Any],
                                 name: str = "", retry_of: str | None = None,
                                 retry_count: int = 0) -> dict:
        task_id = new_id()
        async with self.db.sessions() as session:
            session.add(table(id=task_id, name=name, spec=_json_safe(spec),
                              retry_of=retry_of, retry_count=retry_count))
            await session.commit()
        return await self.get_domain_task(table, task_id)

    async def update_domain_task(self, table: type[Any], task_id: str,
                                 expect_status: str | Collection[str] | None = None,
                                 **fields: Any) -> dict | None:
        async with self.db.sessions() as session:
            row = await session.get(table, task_id)
            if row is None:
                return None
            if expect_status is not None:
                allowed = ({expect_status} if isinstance(expect_status, str)
                           else set(expect_status))
                if row.status not in allowed:
                    return None
            for key, value in fields.items():
                if key not in ("name", "spec", "status", "result", "error",
                               "started_at", "finished_at", "history"):
                    raise ValueError(f"domain task 字段不可更新: {key}")
                if key in ("spec", "result", "history"):
                    value = _json_safe(value)
                setattr(row, key, value)
            if fields.get("status") in TASK_TERMINAL and "finished_at" not in fields:
                row.finished_at = utcnow()
            await session.commit()
        return await self.get_domain_task(table, task_id)

    async def list_domain_tasks(self, table: type[Any], *, status: str | None = None,
                                retry_of: str | None = None, limit: int = 50) -> list[dict]:
        async with self.db.sessions() as session:
            stmt = select(table).order_by(table.created_at.desc())
            if status:
                stmt = stmt.where(table.status == status)
            if retry_of:
                stmt = stmt.where(table.retry_of == retry_of)
            rows = (await session.execute(stmt.limit(limit))).scalars().all()
            return [self._domain_task_dict(r) for r in rows]

    async def get_domain_task(self, table: type[Any], task_id: str) -> dict | None:
        async with self.db.sessions() as session:
            row = await session.get(table, task_id)
            return self._domain_task_dict(row) if row else None

    async def delete_domain_task(self, table: type[Any], task_id: str) -> bool:
        async with self.db.sessions() as session:
            row = await session.get(table, task_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def mark_stale_domain_tasks(self, table: type[Any]) -> int:
        """startup 收敛（原则 8）：running/stopping 残留 → stale。

        pending 是排队任务、未在执行，重启后保留仍可 run——与 bg_task 把
        pending 一并标 error 的语义不同（域任务支持先建后跑）。
        """
        async with self.db.sessions() as session:
            result = await session.execute(
                update(table)
                .where(table.status.in_((TASK_RUNNING, TASK_STOPPING)))
                .values(status=TASK_STALE, error="server restarted before completion",
                        finished_at=utcnow()))
            await session.commit()
            return result.rowcount or 0

    @staticmethod
    def _domain_task_dict(row: Any) -> dict:
        return {
            "id": str(row.id), "name": row.name, "status": row.status,
            "spec": row.spec, "result": row.result, "error": row.error,
            "history": row.history,
            "retry_of": str(row.retry_of) if row.retry_of else None,
            "retry_count": row.retry_count,
            "created_at": aware(row.created_at).isoformat(),
            "started_at": aware(row.started_at).isoformat() if row.started_at else None,
            "finished_at": aware(row.finished_at).isoformat() if row.finished_at else None,
        }

    # --------------------------------------------- version results（§10.5）
    async def upsert_version_result(self, entry: dict[str, Any]) -> dict:
        """同（domain, version, model）覆盖取最新；details 整体替换。"""
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(VersionResultRow).where(
                    VersionResultRow.domain == entry["domain"],
                    VersionResultRow.version == entry["version"],
                    VersionResultRow.model == entry["model"])
            )).scalar_one_or_none()
            if row is None:
                row = VersionResultRow(id=new_id(), domain=entry["domain"],
                                       version=entry["version"],
                                       model=entry.get("model", ""))
                session.add(row)
            row.run_id = entry.get("run_id")
            row.details = entry.get("details") or {}
            await session.commit()
            await session.refresh(row)
            return {"id": str(row.id), "domain": row.domain,
                    "version": row.version, "model": row.model,
                    "run_id": row.run_id, "details": row.details,
                    "updated_at": aware(row.updated_at).isoformat()}

    async def list_version_results(self, *, domain: str, version: str | None = None,
                                   model: str | None = None,
                                   limit: int = 200) -> list[dict]:
        async with self.db.sessions() as session:
            stmt = select(VersionResultRow).where(
                VersionResultRow.domain == domain)
            if version:
                stmt = stmt.where(VersionResultRow.version == version)
            if model:
                stmt = stmt.where(VersionResultRow.model == model)
            rows = (await session.execute(
                stmt.order_by(VersionResultRow.updated_at.desc())
                .limit(limit))).scalars().all()
            return [{"id": str(r.id), "domain": r.domain, "version": r.version,
                     "model": r.model, "run_id": r.run_id, "details": r.details,
                     "updated_at": aware(r.updated_at).isoformat()} for r in rows]

    # ------------------------------------------------------- dim tags（§10.5）
    async def list_dim_tags(self) -> list[dict]:
        async with self.db.sessions() as session:
            rows = (await session.execute(
                select(DimTagRow).order_by(DimTagRow.created_at))).scalars().all()
            return [{"id": str(r.id), "name": r.name,
                     "created_at": aware(r.created_at).isoformat()} for r in rows]

    async def create_dim_tag(self, name: str) -> dict:
        async with self.db.sessions() as session:
            row = DimTagRow(id=new_id(), name=name)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return {"id": str(row.id), "name": row.name,
                    "created_at": aware(row.created_at).isoformat()}

    # ------------------------------------------------- dataset catalog（§12.1）
    async def upsert_dataset_catalog(self, entry: dict[str, Any]) -> dict:
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(DatasetCatalogRow).where(
                    DatasetCatalogRow.type == entry["type"],
                    DatasetCatalogRow.name == entry["name"])
            )).scalar_one_or_none()
            if row is None:
                row = DatasetCatalogRow(id=new_id())
                session.add(row)
            for key in ("type", "name", "version", "source", "ownership",
                        "schema_version", "stats", "capabilities",
                        "path", "notes"):
                if key in entry and entry[key] is not None:
                    setattr(row, key,
                            _json_safe(entry[key])
                            if key in ("stats", "capabilities") else entry[key])
            await session.commit()
            return await self.get_dataset_catalog(str(row.id))

    async def get_dataset_catalog(self, dataset_id: str) -> dict | None:
        async with self.db.sessions() as session:
            row = await session.get(DatasetCatalogRow, dataset_id)
            return self._catalog_dict(row) if row else None

    async def find_dataset_catalog(self, type_: str, name: str) -> dict | None:
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(DatasetCatalogRow).where(
                    DatasetCatalogRow.type == type_,
                    DatasetCatalogRow.name == name)
            )).scalar_one_or_none()
            return self._catalog_dict(row) if row else None

    async def list_dataset_catalog(self, *, type_: str | None = None,
                                   limit: int = 200) -> list[dict]:
        async with self.db.sessions() as session:
            stmt = select(DatasetCatalogRow).order_by(
                DatasetCatalogRow.type, DatasetCatalogRow.name)
            if type_:
                stmt = stmt.where(DatasetCatalogRow.type == type_)
            rows = (await session.execute(stmt.limit(limit))).scalars().all()
            return [self._catalog_dict(r) for r in rows]

    async def delete_dataset_catalog(self, dataset_id: str) -> bool:
        async with self.db.sessions() as session:
            row = await session.get(DatasetCatalogRow, dataset_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    def _catalog_dict(row: DatasetCatalogRow) -> dict:
        return {
            "id": str(row.id), "type": row.type, "name": row.name,
            "version": row.version, "source": row.source,
            "ownership": row.ownership,
            "schema_version": row.schema_version, "stats": row.stats,
            "capabilities": row.capabilities or [],
            "path": row.path, "notes": row.notes,
            "created_at": aware(row.created_at).isoformat(),
            "updated_at": aware(row.updated_at).isoformat(),
        }

    # ------------------------------------------------- test case docs（§15）
    async def create_case_doc(self, entry: dict[str, Any]) -> dict:
        async with self.db.sessions() as session:
            row = TestCaseDocRow(id=new_id(), **{
                k: _json_safe(v) if k in ("script", "dataset_refs", "tags")
                else v
                for k, v in entry.items()
                if k in ("case_no", "domain", "title", "purpose", "steps",
                         "expected", "priority", "risk", "status", "script",
                         "dataset_refs", "tags", "owner")})
            session.add(row)
            await session.commit()
            return await self.get_case_doc(str(row.id))

    async def update_case_doc(self, case_id: str, **fields: Any) -> dict | None:
        allowed = ("title", "purpose", "steps", "expected", "priority",
                   "risk", "status", "script", "dataset_refs", "tags", "owner")
        async with self.db.sessions() as session:
            row = await session.get(TestCaseDocRow, case_id)
            if row is None:
                return None
            for key, value in fields.items():
                if key not in allowed:
                    raise ValueError(f"用例字段不可更新: {key}")
                setattr(row, key,
                        _json_safe(value) if key in ("script", "dataset_refs",
                                                     "tags") else value)
            await session.commit()
        return await self.get_case_doc(case_id)

    async def get_case_doc(self, case_id: str) -> dict | None:
        async with self.db.sessions() as session:
            row = await session.get(TestCaseDocRow, case_id)
            return self._case_doc_dict(row) if row else None

    async def find_case_doc_by_no(self, case_no: str) -> dict | None:
        async with self.db.sessions() as session:
            row = (await session.execute(
                select(TestCaseDocRow).where(TestCaseDocRow.case_no == case_no)
            )).scalar_one_or_none()
            return self._case_doc_dict(row) if row else None

    async def list_case_docs(self, *, domain: str | None = None,
                             status: str | None = None,
                             priority: str | None = None,
                             limit: int = 200) -> list[dict]:
        async with self.db.sessions() as session:
            stmt = select(TestCaseDocRow).order_by(TestCaseDocRow.case_no)
            if domain:
                stmt = stmt.where(TestCaseDocRow.domain == domain)
            if status:
                stmt = stmt.where(TestCaseDocRow.status == status)
            if priority:
                stmt = stmt.where(TestCaseDocRow.priority == priority)
            rows = (await session.execute(stmt.limit(limit))).scalars().all()
            return [self._case_doc_dict(r) for r in rows]

    async def delete_case_doc(self, case_id: str) -> bool:
        async with self.db.sessions() as session:
            row = await session.get(TestCaseDocRow, case_id)
            if row is None:
                return False
            await session.execute(
                sa_delete(CaseRunRow).where(CaseRunRow.case_id == case_id))
            await session.delete(row)
            await session.commit()
            return True

    async def create_case_run(self, entry: dict[str, Any]) -> dict:
        run_id = new_id()
        async with self.db.sessions() as session:
            session.add(CaseRunRow(id=run_id, **{
                k: _json_safe(v) if k in ("key_metrics", "evidence") else v
                for k, v in entry.items()
                if k in ("case_id", "version", "task_id", "run_id", "verdict",
                         "key_metrics", "evidence", "notes")}))
            await session.commit()
        runs = await self.list_case_runs(case_id=entry["case_id"], limit=1)
        return runs[0] if runs else {"id": run_id}

    async def list_case_runs(self, *, case_id: str | None = None,
                             version: str | None = None,
                             verdict: str | None = None,
                             limit: int = 200) -> list[dict]:
        async with self.db.sessions() as session:
            stmt = select(CaseRunRow).order_by(CaseRunRow.executed_at.desc())
            if case_id:
                stmt = stmt.where(CaseRunRow.case_id == case_id)
            if version:
                stmt = stmt.where(CaseRunRow.version == version)
            if verdict:
                stmt = stmt.where(CaseRunRow.verdict == verdict)
            rows = (await session.execute(stmt.limit(limit))).scalars().all()
            return [self._case_run_dict(r) for r in rows]

    async def latest_case_run(self, case_id: str,
                              version: str | None = None) -> dict | None:
        runs = await self.list_case_runs(case_id=case_id, version=version,
                                         limit=1)
        return runs[0] if runs else None

    async def delete_case_runs_for_case(self, case_id: str) -> int:
        async with self.db.sessions() as session:
            result = await session.execute(
                sa_delete(CaseRunRow).where(CaseRunRow.case_id == case_id))
            await session.commit()
            return result.rowcount or 0

    @staticmethod
    def _case_doc_dict(row: TestCaseDocRow) -> dict:
        return {
            "id": str(row.id), "case_no": row.case_no, "domain": row.domain,
            "title": row.title, "purpose": row.purpose, "steps": row.steps,
            "expected": row.expected, "priority": row.priority,
            "risk": row.risk, "status": row.status, "script": row.script,
            "dataset_refs": row.dataset_refs or [], "tags": row.tags or [],
            "owner": row.owner,
            "created_at": aware(row.created_at).isoformat(),
            "updated_at": aware(row.updated_at).isoformat(),
        }

    @staticmethod
    def _case_run_dict(row: CaseRunRow) -> dict:
        return {
            "id": str(row.id), "case_id": str(row.case_id),
            "version": row.version, "task_id": str(row.task_id) if row.task_id else "",
            "run_id": str(row.run_id) if row.run_id else None,
            "verdict": row.verdict, "key_metrics": row.key_metrics,
            "evidence": row.evidence, "notes": row.notes,
            "executed_at": aware(row.executed_at).isoformat(),
        }
