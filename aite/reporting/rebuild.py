"""Rebuild a RunReport from persisted data (for `aite report show`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aite.core.types import (
    CaseMetricRecord,
    MetricSummary,
    RunReport,
    utcnow,
)

if TYPE_CHECKING:
    from aite.core.interfaces import Repository


async def rebuild_report(repo: Repository, run_id: str) -> RunReport | None:
    run = await repo.get_run(run_id)
    if run is None:
        return None
    summaries = [
        MetricSummary(
            metric=s["metric"], dataset=s["dataset"], group=s["group"],
            aggregation=s["aggregation"], value=s["value"], threshold=s["threshold"],
            passed=s["passed"], count=s["count"], pass_rate=s["pass_rate"],
        )
        for s in await repo.list_metric_summaries(run_id)
    ]
    case_metrics = [
        CaseMetricRecord(
            case_id=str(m.get("case_result_id")), metric=m["metric"], value=m["value"],
            passed=m["passed"], raw=m.get("raw") or {}, error=m.get("error"),
        )
        for m in await repo.list_metric_results(run_id)
    ]
    return RunReport(
        run_id=run_id, run_name=run["run_name"], status=run["status"],
        pass_strategy=(run.get("config") or {}).get("pass_strategy", "all_of"),
        started_at=_parse_dt(run.get("started_at")),
        finished_at=_parse_dt(run.get("finished_at")),
        total_cases=run.get("total_cases", 0), error_cases=run.get("error_cases", 0),
        total_cost_usd=run.get("cost_usd", 0.0) or 0.0,
        summaries=summaries, case_metrics=case_metrics,
        config=run.get("config") or {},
        details={"restored_from_db": True, "note": "case metrics restored without dataset/group labels"},
    )


def _parse_dt(value: Any):
    if value is None:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return utcnow()
