"""Web 路由的细粒度业务编排（P2-3 路由瘦身：解析/聚合/文件 IO 收口于此）。

与 run_service（执行编排）并列；路由只做参数校验 → 调本层 → 返回。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def list_perf_datasets() -> list[dict]:
    """性能场景数据集产物清单（模块 J）：stats 摘要 + 时间倒序。

    磁盘枚举收口本层（原则 1：路由不直接读磁盘）；产物目录见
    config.DEFAULT_PERFDATA_DIR。
    """
    from aite import config

    base = Path(config.DEFAULT_PERFDATA_DIR)
    if not base.is_dir():
        return []
    items = []
    for stats_path in sorted(base.glob("*.stats.json"),
                             key=lambda p: p.stat().st_mtime, reverse=True):
        file_path = stats_path.with_name(
            stats_path.name[:-len(".stats.json")] + ".jsonl")
        items.append({
            "file": file_path.name,
            "path": str(file_path),
            "stats": json.loads(stats_path.read_text(encoding="utf-8")),
            "created_at": datetime.fromtimestamp(
                stats_path.stat().st_mtime).isoformat(timespec="seconds"),
        })
    return items


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


# ------------------------------------------------------------ 域过滤（§V1 规划 10.3）
DOMAINS = ("functional", "accuracy", "perf", "reliability")
_PERF_ENGINES = frozenset({"evalscope-perf", "locust"})


def run_domain(run: dict) -> str:
    """run → 所属域（单一事实来源，/runs 与 /stats 的 domain 过滤共用）。

    engine 取 config_snapshot.engine（工具域 suite raw 原样入库）；带 engine
    按引擎归类，无 engine = 自研引擎评测 run → accuracy；self-built run 的
    config_snapshot 带 chaos 声明 → reliability。
    """
    config = run.get("config") or {}
    engine = config.get("engine")
    if engine in _PERF_ENGINES:
        return "perf"
    if engine == "pytest":
        return "functional"
    if engine:
        return "accuracy"          # evalscope / evalscope-rageval 等工具评测
    if config.get("chaos"):
        return "reliability"
    return "accuracy"


def filter_runs_by_domain(runs: list[dict], domain: str) -> list[dict]:
    """按域过滤 run 列表；未知域显式报错（原则 9）。"""
    if domain not in DOMAINS:
        raise ValueError(f"未知域 {domain!r}（合法：{'/'.join(DOMAINS)}）")
    return [r for r in runs if run_domain(r) == domain]


async def import_functional_dataset(repo: Any, *, name: str, jsonl: str,
                                    notes: str = "", force: bool = False) -> dict:
    """REST 摄取功能数据集：JSONL 文本 → 临时文件 → 域校验/落盘/登记。"""
    import tempfile

    from aite.functional.ingest import ingest_functional

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8") as f:
        f.write(jsonl)
        tmp_path = f.name
    try:
        return await ingest_functional(repo, tmp_path, name, notes=notes,
                                       force=force)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


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


# 逐样本字段截断上限：详情页只需要可判定 + 可定位的量，全文走 work_dir 落盘产物
_SAMPLE_CLIP = {"question": 300, "prediction": 2000, "explanation": 500}


def _clip(text: Any, limit: int) -> str:
    if text is None:
        return ""
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + "…"


def _sample_message_text(messages: Any) -> str:
    """messages 首条 user 内容 → 纯文本摘要（content 可能是字符串或分块列表）。"""
    for m in messages or []:
        if (m or {}).get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = "\n".join(str(b.get("text") or "") for b in content
                                if isinstance(b, dict))
        return str(content or "")
    return ""


def _sample_row(raw: dict, dataset: str) -> dict:
    """reviews jsonl 行（evalscope 逐样本评分）→ 详情页行；缺字段全部容忍。"""
    ss = raw.get("sample_score") or {}
    sc = ss.get("score") or {}
    values = sc.get("value") if isinstance(sc.get("value"), dict) else {}
    main = sc.get("main_score_name")
    value = values.get(main) if main else None
    if value is None and values:
        value = next(iter(values.values()), None)
    meta = ss.get("sample_metadata") or raw.get("sample_metadata") or {}
    return {
        "dataset": dataset,
        "index": raw.get("index"),
        "sample_id": ss.get("sample_id"),
        "score": value,
        "status": sc.get("status"),
        "passed": (value >= 1.0) if isinstance(value, (int, float)) else None,
        "extracted_prediction": _clip(sc.get("extracted_prediction"), 200),
        "prediction": _clip(sc.get("prediction"), _SAMPLE_CLIP["prediction"]),
        "judge_summary": sc.get("judge_summary"),
        "explanation": _clip(sc.get("explanation"), _SAMPLE_CLIP["explanation"]),
        "target": _clip(raw.get("target"), 200),
        "correct_answer": _clip(meta.get("correct_answer"), 200),
        "question": _clip(_sample_message_text(raw.get("messages")),
                          _SAMPLE_CLIP["question"]),
    }


def load_run_samples(run: dict, *, limit: int = 200, offset: int = 0,
                     wrong_only: bool = False) -> dict | None:
    """读 run.details.work_dir 下 reviews/**/*.jsonl（evalscope 逐样本评分契约）。

    返回 {items, total, wrong, datasets}；run 无 work_dir 或无逐样本产物
    返回 None（404 语义由路由定——自研引擎域与旧运行没有该产物）。坏行跳过
    不致命（单行损坏不该拖垮整页）。wrong_only 只留 passed=False 的行。
    """
    work_dir = (run.get("details") or {}).get("work_dir")
    if not work_dir:
        return None
    files = sorted(Path(work_dir).glob("reviews/**/*.jsonl"))
    if not files:
        return None
    rows: list[dict] = []
    wrong = 0
    for p in files:
        dataset = p.stem.removesuffix("_default")
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                row = _sample_row(raw, dataset)
                if row["passed"] is False:
                    wrong += 1
                elif wrong_only:
                    continue
                rows.append(row)
    return {"items": rows[offset:offset + limit], "total": len(rows),
            "wrong": wrong,
            "datasets": sorted({r["dataset"] for r in rows})}


__all__ = ["compute_platform_stats", "filter_runs_by_domain",
           "import_functional_dataset", "load_run_samples",
           "load_run_timeseries",
           "parse_jsonl_records", "run_domain", "select_manual_tasks",
           "to_import_cases"]
