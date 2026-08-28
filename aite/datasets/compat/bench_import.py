"""Bench result import (subprocess-bridge layer 2).

`aite bench import --format lm-eval results.json` ingests results produced by
the official lm-eval / evalscope CLIs into the aite DB as a normal run so
trends and reports keep working.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aite.core.types import MetricSummary, utcnow

# metric name normalization -> (aite metric, higher_is_better)
_LM_EVAL_METRICS = {
    "acc": ("accuracy", True), "acc_norm": ("accuracy", True),
    "exact_match": ("exact_match", True), "em": ("exact_match", True),
    "f1": ("token_f1", True), "rouge_l": ("rouge_l", True),
    "bleu": ("bleu", True), "pass@1": ("exact_match", True),
    "mse": ("mse", False), "mae": ("mae", False), "perplexity": ("perplexity", False),
}


async def import_results(
    repo: Any, path: str | Path, *, fmt: str, run_name: str | None = None,
) -> str:
    """Parse an lm-eval / evalscope results file and persist a synthetic run."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"results file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    fmt = fmt.lower().replace("-", "").replace("_", "")
    if fmt in ("lmeval", "lmevaluationharness", "lm-eval"):
        run_name_out, summaries, meta = _parse_lm_eval(data)
    elif fmt == "evalscope":
        run_name_out, summaries, meta = _parse_evalscope(data)
    else:
        raise ValueError(f"unsupported format '{fmt}' (use lm-eval | evalscope)")

    run_name = run_name or run_name_out
    suite_id = await repo.upsert_suite(run_name, {"source": "bench-import", **meta})
    run_id = await repo.create_run(run_name, {"source": "bench-import", **meta}, suite_id)
    for s in summaries:
        await repo.save_metric_summary(run_id, s)
    passed = all(s.passed for s in summaries if s.passed is not None) if summaries else True
    await repo.update_run(
        run_id, status="passed" if passed else "failed",
        total_cases=meta.get("total_cases", 0), error_cases=0,
        finished_at=utcnow(),
    )
    return run_id


def _parse_lm_eval(data: dict) -> tuple[str, list[MetricSummary], dict]:
    """lm-eval --output_path results.json schema: {results: {task: {metric: val,
    stderr_metric: ...}}, configs: {...}, n-samples: {task: {effective: n}}}."""
    results = data.get("results", data)
    n_samples = data.get("n-samples", {})
    summaries: list[MetricSummary] = []
    first_task = ""
    for task, metrics in results.items():
        if task.startswith("#") or not isinstance(metrics, dict):
            continue
        first_task = first_task or task
        for key, value in metrics.items():
            if key.startswith("stderr") or not isinstance(value, (int, float)):
                continue
            aite_metric = _LM_EVAL_METRICS.get(key)
            if aite_metric is None and key.startswith("acc"):
                aite_metric = ("accuracy", True)
            if aite_metric is None:
                continue
            name, higher = aite_metric
            summaries.append(MetricSummary(
                metric=name, dataset=task, group=None, aggregation="mean",
                value=round(float(value), 6), higher_is_better=higher,
                count=int(n_samples.get(task, {}).get("effective", 0)) if isinstance(
                    n_samples.get(task), dict) else 0,
            ))
    run_name = f"lm-eval-{first_task}" if first_task else "lm-eval-import"
    meta = {"format": "lm-eval", "tasks": list(results.keys())[:20],
            "total_cases": sum(
                v.get("effective", 0) for v in n_samples.values()
                if isinstance(v, dict))}
    return run_name, summaries, meta


def _parse_evalscope(data: dict) -> tuple[str, list[MetricSummary], dict]:
    """evalscope report json: {report: {dataset: [{metric, score, stderr...}]}}
    (older: {metrics: {...}} / final_report)."""
    report = data.get("report") or data.get("final_report") or {}
    if not report and isinstance(data.get("metrics"), dict):
        report = {"task": data["metrics"]}
    summaries: list[MetricSummary] = []
    dataset_names: list[str] = []
    for dataset, rows in report.items():
        if not isinstance(rows, list):
            rows = [rows] if isinstance(rows, dict) else []
        dataset_names.append(str(dataset))
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_metric = str(row.get("metric", row.get("name", "")))
            score = row.get("score", row.get("value"))
            if score is None:
                continue
            mapped = _LM_EVAL_METRICS.get(raw_metric, (raw_metric.replace("-", "_"), True))
            name, higher = mapped
            summaries.append(MetricSummary(
                metric=name, dataset=str(dataset), group=None, aggregation="mean",
                value=round(float(score), 6), higher_is_better=higher,
                count=int(row.get("total", 0)),
            ))
    run_name = f"evalscope-{dataset_names[0]}" if dataset_names else "evalscope-import"
    meta = {"format": "evalscope", "datasets": dataset_names[:20],
            "total_cases": sum(s.count for s in summaries)}
    return run_name, summaries, meta
