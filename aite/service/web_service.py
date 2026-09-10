"""Web 路由的细粒度业务编排（P2-3 路由瘦身：解析/聚合/文件 IO 收口于此）。

与 run_service（执行编排）并列；路由只做参数校验 → 调本层 → 返回。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def parse_jsonl_records(text: str) -> list[dict]:
    """JSONL 文本 → 记录列表；坏行显式报错（原则 9，带行号定位）。"""
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL line: {exc}") from exc
    return records


def to_import_cases(records: list[dict], fields: dict[str, str]) -> list[Any]:
    """上传记录 → TestCase 列表（经 LocalFileDataset 的公开转换入口）。"""
    from aite.core.loader import DatasetConfig
    from aite.datasets.local import LocalFileDataset

    config_ = DatasetConfig(type="local", path="uploaded.jsonl", fields=fields)
    cases = LocalFileDataset().to_cases(records, config_, "uploaded")
    for case in cases:
        case.dataset = "uploaded"
    return cases


def select_manual_tasks(results: list[dict], case_result_ids: list[int] | None,
                        limit: int | None) -> list[dict]:
    """人工评测候选筛选：base 变体 → 指定 id（否则取前 N）。"""
    picked = [r for r in results if r.get("variant", "base") == "base"]
    if case_result_ids:
        return [r for r in picked if r["id"] in set(case_result_ids)]
    return picked[:limit]


def compute_platform_stats(runs: list[dict], active_tasks: int) -> dict:
    """平台统计聚合（runs_by_status / 成本合计）。"""
    statuses: dict[str, int] = {}
    for r in runs:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    return {
        "runs_total": len(runs),
        "runs_by_status": statuses,
        "total_cost_usd": round(sum(r.get("cost_usd", 0) or 0 for r in runs), 6),
        "active_tasks": active_tasks,
    }


def load_run_timeseries(run: dict) -> dict | None:
    """按 run 详情读取压测时序文件；无产出返回 None（404 语义由路由定）。"""
    work_dir = (run.get("details") or {}).get("work_dir")
    ts_path = Path(work_dir) / "timeseries.json" if work_dir else None
    if not ts_path or not ts_path.is_file():
        return None
    try:
        return json.loads(ts_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError(f"corrupted timeseries: {exc}") from exc


__all__ = ["compute_platform_stats", "load_run_timeseries", "parse_jsonl_records",
           "select_manual_tasks", "to_import_cases"]
