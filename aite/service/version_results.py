"""版本测试结果归档与大屏聚合（V1-PLAN §10.5）。

归档：functional 域任务（spec = suite raw）执行完成后，从 RunReport.summaries
提取场景级 pass_rate（dataset = ``pytest:<场景>``），按（version × model）同键
覆盖落库 version_result。spec 顶层 ``version`` 缺省 = 未标注版本 → 不归档；
归档失败只告警不推翻任务结果（原则 9：显式告警，来源可追溯）。

大屏：/functional/quality/board 只读 version_result 聚合——目标版本 vs 基线
版本的维度并集矩阵 + Δ + 判定（Δ ≥ 0 达标 / < 0 劣化，与精度劣化同口径）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aite.service.version_results")
PYTEST_PREFIX = "pytest:"


def _model_of(spec: dict) -> str:
    target = spec.get("target") or {}
    if isinstance(target, dict):
        return str(target.get("profile") or target.get("model") or "")
    return str(target or "")


def _weighted_overall(rows: list[tuple[float | None, int]]) -> float | None:
    num = den = 0
    for value, count in rows:
        if value is None:
            continue
        num += value * count
        den += count
    return round(num / den, 6) if den else None


def extract_dimensions(report: Any) -> dict[str, float]:
    """RunReport.summaries → {场景: pass_rate}（dataset = pytest:<场景>）。"""
    dims: dict[str, float] = {}
    for s in getattr(report, "summaries", None) or []:
        dataset = getattr(s, "dataset", None) or ""
        if dataset.startswith(PYTEST_PREFIX) and s.value is not None:
            dims[dataset[len(PYTEST_PREFIX):]] = round(float(s.value), 6)
    return dims


async def archive_functional_result(repo: Any, *, spec: dict,
                                    report: Any) -> dict | None:
    """spec 顶层 version 标注且产出场景级行 → 覆盖归档；否则 None（不归档）。"""
    version = str(spec.get("version") or "").strip()
    if not version:
        logger.debug("任务未标注 version，跳过版本结果归档（run=%s）",
                     getattr(report, "run_id", None))
        return None
    if getattr(report, "status", "") == "error":
        logger.warning("functional run 状态为 error（执行崩溃），跳过版本归档"
                       "（run=%s，半截数据不入矩阵）", report.run_id)
        return None
    dimensions = extract_dimensions(report)
    if not dimensions:
        logger.warning("functional 任务无场景级 pass_rate 行，跳过归档（run=%s）",
                       getattr(report, "run_id", None))
        return None
    counts = {s.dataset[len(PYTEST_PREFIX):]: s.count
              for s in report.summaries
              if (s.dataset or "").startswith(PYTEST_PREFIX)}
    overall = _weighted_overall([(dimensions[s], counts.get(s, 0))
                                 for s in dimensions])
    details = {"run_id": getattr(report, "run_id", None),
               "run_name": getattr(report, "run_name", ""),
               "status": getattr(report, "status", ""),
               "dimensions": dimensions,
               "overall": overall,
               "cases": sum(counts.values())}
    return await repo.upsert_version_result({
        "domain": "functional", "version": version,
        "model": _model_of(spec), "run_id": details["run_id"],
        "details": details})


async def archive_maybe(repo: Any, spec: dict, report: Any) -> None:
    """SuiteTaskExecutor 钩子：仅 functional 域归档；失败告警不抛（原则 9）。"""
    if spec.get("module") != "functional":
        return
    try:
        await archive_functional_result(repo, spec=spec, report=report)
    except Exception as exc:   # 归档失败不推翻任务结果，显式告警可追溯
        logger.warning("版本结果归档失败（不影响任务结果）: %s", exc)


def compute_board(rows: list[dict], target: str, baseline: str) -> dict:
    """纯函数：版本归档行 → 维度并集矩阵 + Δ 判定 + KPI。缺版本显式报错。"""
    by_version = {r["version"]: r for r in rows}
    for name, version in (("目标版本", target), ("基线版本", baseline)):
        if version not in by_version:
            raise ValueError(f"{name} {version!r} 无归档结果")
    t_details = by_version[target].get("details") or {}
    b_details = by_version[baseline].get("details") or {}
    t_dims: dict = t_details.get("dimensions") or {}
    b_dims: dict = b_details.get("dimensions") or {}
    matrix = []
    for dim in sorted(set(t_dims) | set(b_dims)):
        tv, bv = t_dims.get(dim), b_dims.get(dim)
        if tv is None or bv is None:
            verdict = "新增" if tv is not None else "缺失"
            delta = None
        else:
            delta = round(tv - bv, 6)
            verdict = "达标" if delta >= 0 else "劣化"
        matrix.append({"scenario": dim, "target": tv, "baseline": bv,
                       "delta": delta, "verdict": verdict})
    degraded = sum(1 for m in matrix if m["verdict"] == "劣化")
    improved = sum(1 for m in matrix if m["verdict"] == "达标"
                   and (m["delta"] or 0) > 0)
    added = sum(1 for m in matrix if m["verdict"] == "新增")
    missing = sum(1 for m in matrix if m["verdict"] == "缺失")
    t_overall, b_overall = t_details.get("overall"), b_details.get("overall")
    return {"target": target, "baseline": baseline,
            "model": by_version[target].get("model", ""),
            "kpi": {"target_overall": t_overall, "baseline_overall": b_overall,
                    "delta": (None if t_overall is None or b_overall is None
                              else round(t_overall - b_overall, 6)),
                    "dimensions": len(matrix), "degraded": degraded,
                    "improved": improved, "added": added, "missing": missing},
            "dimensions": matrix,
            "meta": {"target_run": t_details.get("run_id"),
                     "baseline_run": b_details.get("run_id")}}


async def board(repo: Any, *, model: str, target: str, baseline: str) -> dict:
    rows = await repo.list_version_results(domain="functional", model=model)
    return compute_board(rows, target, baseline)
