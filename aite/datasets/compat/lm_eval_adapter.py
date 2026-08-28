"""lm-evaluation-harness task YAML -> aite suite fragment.

Supports the common task schema::

    task: mmlu_abstract_algebra
    dataset_path: cais/mmlu
    dataset_name: abstract_algebra
    test_split: test
    doc_to_text: "Question: {{question}}\nAnswer:"
    doc_to_choice: "{{choices}}"
    doc_to_target: "{{answer}}"       # index into choices
    metric_list:
      - metric: acc
      - metric: f1

Jinja templates are converted when they are simple field references; complex
templates fall back to raw-field mapping with a warning recorded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_METRIC_MAP = {
    "acc": ("choice_accuracy", {}),
    "acc_norm": ("choice_accuracy", {}),
    "exact_match": ("exact_match", {}),
    "f1": ("token_f1", {}),
    "em": ("exact_match", {}),
    "rouge_l": ("rouge_l", {}),
    "bleu": ("bleu", {}),
}

_FIELD_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def convert_lm_eval_task(yaml_path: str | Path, *,
                         threshold: float | None = None) -> dict[str, Any]:
    """Return an aite suite-config fragment (datasets + metrics) for one task file."""
    data = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid lm-eval task file: {yaml_path}")
    task_name = data.get("task") or Path(yaml_path).stem

    dataset: dict[str, Any] = {"type": "hf_hub", "id": data.get("dataset_path")}
    if data.get("dataset_name"):
        dataset["kwargs"] = {"name": data["dataset_name"]}
    dataset["split"] = data.get("test_split") or data.get("validation_split") or "test"

    warnings: list[str] = []
    doc_to_text = data.get("doc_to_text") or ""
    doc_to_choice = data.get("doc_to_choice")
    doc_to_target = data.get("doc_to_target")
    fields: dict[str, str] = {}

    text_fields = _FIELD_RE.findall(str(doc_to_text))
    if len(text_fields) == 1 and str(doc_to_text).strip() == \
            f"{{{{{text_fields[0]}}}}}":
        fields["input"] = text_fields[0]
    elif text_fields:
        # composite template - keep question field if present, warn otherwise
        if "question" in text_fields:
            fields["input"] = "question"
        warnings.append(f"doc_to_text template not fully convertible: {doc_to_text!r}")
        dataset["kwargs"] = {**(dataset.get("kwargs") or {}), "aite_text_template": str(doc_to_text)}

    if isinstance(doc_to_choice, str) and (m := _FIELD_RE.fullmatch(doc_to_choice.strip())):
        dataset["kwargs"] = {**(dataset.get("kwargs") or {}), "mcq_choices_field": m.group(1)}
    if isinstance(doc_to_target, str) and (m := _FIELD_RE.fullmatch(doc_to_target.strip())):
        fields["expected"] = m.group(1)
    elif isinstance(doc_to_target, int):
        dataset["kwargs"] = {**(dataset.get("kwargs") or {}), "answer_index": True}
    dataset["fields"] = fields

    metrics: list[dict[str, Any]] = []
    for item in data.get("metric_list") or [{"metric": "acc"}]:
        name = item.get("metric", "acc") if isinstance(item, dict) else str(item)
        if name in _METRIC_MAP:
            mapped, params = _METRIC_MAP[name]
            if mapped == "choice_accuracy":
                params = {}
            entry: dict[str, Any] = {mapped: params}
            if threshold is not None:
                entry[mapped]["threshold"] = threshold
            metrics.append(entry)
        else:
            warnings.append(f"metric '{name}' has no builtin mapping; skipped")

    if not metrics:
        metrics = [{"choice_accuracy": {}} if doc_to_choice else {"exact_match": {}}]

    return {
        "run_name": f"lm-eval-{task_name}",
        "description": f"converted from lm-eval task {task_name}",
        "datasets": [dataset],
        "metrics": metrics,
        "details": {"source": "lm-eval", "task": task_name, "warnings": warnings},
    }
