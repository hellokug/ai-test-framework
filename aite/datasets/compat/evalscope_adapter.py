"""evalscope task config -> aite suite fragment.

Handles the common evalscope task schema (their yaml uses ``dataset`` blocks
with ``args`` and per-metric dicts). Best-effort; complex scoring (e.g. exact
CodeXGLUE rules) maps to the closest builtin metric and is recorded in details.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_METRIC_MAP = {
    "acc": "choice_accuracy",
    "accuracy": "choice_accuracy",
    "exact_match": "exact_match",
    "em": "exact_match",
    "f1": "token_f1",
    "rouge_l": "rouge_l",
    "rouge": "rouge_l",
    "bleu": "bleu",
    "pass@1": "exact_match",
    "win_rate": "exact_match",
}


def convert_evalscope_task(yaml_path: str | Path, *,
                           threshold: float | None = None) -> dict[str, Any]:
    data = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid evalscope task file: {yaml_path}")
    task_name = data.get("name") or data.get("id") or Path(yaml_path).stem

    dataset_block = data.get("dataset") or {}
    args = dataset_block.get("args") or {}
    if isinstance(args, list):
        args = args[0] if args else {}
    dataset: dict[str, Any] = {"type": "hf_hub"}
    hub_path = args.get("path") or args.get("dataset_path") or args.get("name")
    dataset["id"] = hub_path
    if args.get("name") and args.get("name") != hub_path:
        dataset["kwargs"] = {"name": args["name"]}
    dataset["split"] = args.get("split") or dataset_block.get("split") or "test"

    fields: dict[str, str] = {}
    prompts = dataset_block.get("prompts") or []
    if isinstance(prompts, list) and prompts:
        dataset["kwargs"] = {**(dataset.get("kwargs") or {}),
                             "aite_text_template": str(prompts[0])}
    for aite_field, key in (("input", "query"), ("input", "question"),
                            ("expected", "reference"), ("expected", "answer")):
        if key in args and aite_field not in fields:
            fields[aite_field] = args[key]
    dataset["fields"] = fields

    warnings: list[str] = []
    metrics: list[dict[str, Any]] = []
    scoring = data.get("scoring") or data.get("metrics") or ["acc"]
    if isinstance(scoring, dict):
        scoring = list(scoring.keys())
    if isinstance(scoring, str):
        scoring = [scoring]
    for name in scoring:
        mapped = _METRIC_MAP.get(str(name))
        if mapped:
            params: dict[str, Any] = {}
            if threshold is not None:
                params["threshold"] = threshold
            metrics.append({mapped: params})
        else:
            warnings.append(f"evalscope metric '{name}' has no builtin mapping; skipped")
    if not metrics:
        metrics = [{"exact_match": {}}]

    few_shot = int(dataset_block.get("few_shot", 0) or 0)
    if few_shot:
        warnings.append(f"few_shot={few_shot} is not applied by aite adapter")

    return {
        "run_name": f"evalscope-{task_name}",
        "description": f"converted from evalscope task {task_name}",
        "datasets": [dataset],
        "metrics": metrics,
        "details": {"source": "evalscope", "task": task_name, "warnings": warnings},
    }
