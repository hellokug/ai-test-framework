"""Local file datasets: JSONL (preferred) / JSON / CSV with field mapping + sampling.

Record -> TestCase mapping (defaults, override via ``fields:``):
    input    <- input | question | instruction | prompt
    expected <- expected | answer | gold | output | label
    metadata <- whole record (minus mapped fields)
    retrieval_context <- retrieval_context | contexts | context
``id`` <- id | case_id | metadata.id or auto ``<stem>-<n>``.
"""

from __future__ import annotations

import csv
import json
import random
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from aite.core.registry import register_dataset
from aite.core.types import TestCase

if TYPE_CHECKING:
    from aite.core.loader import DatasetConfig

_INPUT_KEYS = ("input", "question", "instruction", "prompt", "query", "user")
_EXPECTED_KEYS = ("expected", "answer", "gold", "output", "label", "target", "response")
_CONTEXT_KEYS = ("retrieval_context", "contexts", "context", "documents")


@register_dataset("local")
class LocalFileDataset:
    name: ClassVar[str] = "local"

    def load(self, config: DatasetConfig) -> AsyncIterator[TestCase]:
        async def _gen():
            path = Path(config.path or "")
            if not path.exists():
                raise FileNotFoundError(f"dataset file not found: {path}")
            records = _read_records(path)
            dataset_label = str(path)
            for case in self.to_cases(records, config, dataset_label):
                yield case
        return _gen()

    def to_cases(self, records: list[dict], config: DatasetConfig,
                  dataset_label: str) -> list[TestCase]:
        mapping = config.fields or {}
        stem = Path(config.path or "case").stem
        cases: list[TestCase] = []
        for i, rec in enumerate(records):
            input_key = mapping.get("input") or next((k for k in _INPUT_KEYS if k in rec), None)
            if input_key is None:
                continue
            expected_key = mapping.get("expected") or next(
                (k for k in _EXPECTED_KEYS if k in rec and k != input_key), None)
            context_key = mapping.get("retrieval_context") or next(
                (k for k in _CONTEXT_KEYS if k in rec and k != input_key), None)
            case_id = str(rec.get(mapping.get("id", "id") if mapping.get("id") else "id")
                          or rec.get("case_id") or f"{stem}-{i}")
            metadata = {k: v for k, v in rec.items()
                        if k not in (input_key, expected_key, context_key, "id", "case_id")}
            if metadata.get("metadata") and isinstance(metadata["metadata"], dict):
                merged = dict(metadata.pop("metadata"))
                merged.update({k: v for k, v in metadata.items() if k != "metadata"})
                metadata = merged
            expected = rec.get(expected_key) if expected_key else None
            cases.append(TestCase(
                id=case_id,
                input=rec[input_key],
                expected=expected,
                metadata=metadata,
                retrieval_context=_as_context(rec.get(context_key)) if context_key else None,
                dataset=dataset_label,
            ))
        cases = _apply_sampling(cases, config)
        if config.limit:
            cases = cases[: config.limit]
        return cases


def _read_records(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8-sig")
    if suffix in (".jsonl", ".ndjson"):
        records = []
        for line_no, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_no} of {path}: {exc}") from exc
        return records
    if suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("data", "cases", "items", "records", "examples"):
                if isinstance(data.get(key), list):
                    return data[key]
            raise ValueError(f"JSON object in {path} has no list field (data/cases/items/...)")
        if isinstance(data, list):
            return data
        raise ValueError(f"unsupported JSON structure in {path}")
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        return [_coerce_csv(row) for row in rows]
    raise ValueError(f"unsupported dataset format: {suffix} (use .jsonl/.json/.csv)")


def _coerce_csv(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v is None:
            out[k] = v
            continue
        stripped = v.strip()
        if stripped.startswith(("[", "{")):
            try:
                out[k] = json.loads(stripped)
                continue
            except json.JSONDecodeError:
                pass
        if re.fullmatch(r"-?\d+", stripped):
            out[k] = int(stripped)
        elif re.fullmatch(r"-?\d+\.\d+", stripped):
            out[k] = float(stripped)
        elif stripped.lower() in ("true", "false"):
            out[k] = stripped.lower() == "true"
        else:
            out[k] = v
    return out


def _as_context(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _apply_sampling(cases: list[TestCase], config: DatasetConfig) -> list[TestCase]:
    sample = config.sample
    if sample is None:
        return cases
    rng = random.Random(sample.seed)
    pool = list(cases)
    if sample.shuffle:
        rng.shuffle(pool)
    if sample.n is not None and sample.n < len(pool):
        return pool[: sample.n]
    return pool
