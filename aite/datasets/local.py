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

from aite.core.interfaces import Dataset
from aite.core.registry import register_dataset
from aite.core.types import TestCase

if TYPE_CHECKING:
    from aite.core.loader import DatasetConfig

_INPUT_KEYS = ("input", "question", "instruction", "prompt", "query", "user")
_EXPECTED_KEYS = ("expected", "answer", "gold", "output", "label", "target", "response")
_CONTEXT_KEYS = ("retrieval_context", "contexts", "context", "documents")
# 对话数组记录（ShareGPT conversations / OpenAI messages 等）的形状约定：
# 已知键名优先，缺省扫描所有 list-of-dict 字段；话轮文本/角色键取常见命名。
_CONVERSATION_KEYS = ("conversations", "conversation", "messages", "message",
                      "chat", "history", "dialog", "dialogue", "turns")
_TURN_TEXT_KEYS = ("value", "content", "text", "message", "response")
_TURN_ROLE_KEYS = ("from", "role", "sender", "who", "speaker")
_HUMAN_ROLES = ("human", "user", "prompter", "client", "person", "q")


@register_dataset("local")
class LocalFileDataset(Dataset):
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


def _record_turns(record: dict) -> list[tuple[str, str]]:
    """对话数组字段 → (话轮文本, 角色) 列表（顺序保留）；非对话记录返回 []。

    ShareGPT（conversations/from/value）与 OpenAI 风格（messages/role/content）
    都覆盖；角色键缺失记空串，空话轮跳过。
    """
    groups = [record[k] for k in _CONVERSATION_KEYS if isinstance(record.get(k), list)]
    if not groups:
        groups = [v for v in record.values()
                  if isinstance(v, list) and any(isinstance(t, dict) for t in v)]
    turns: list[tuple[str, str]] = []
    for group in groups:
        for turn in group:
            if not isinstance(turn, dict):
                continue
            text = next((turn[k] for k in _TURN_TEXT_KEYS
                         if isinstance(turn.get(k), str) and turn[k].strip()), None)
            if not text:
                continue
            role = next((str(turn[k]).strip().lower() for k in _TURN_ROLE_KEYS
                         if isinstance(turn.get(k), str) and turn[k].strip()), "")
            turns.append((text, role))
    return turns


def conversation_texts(record: dict) -> list[str]:
    """对话数组记录 → 全部话轮文本（语料池原料，human/assistant 都算真实语料）。"""
    return [text for text, _ in _record_turns(record)]


def human_prompt(record: dict) -> str | None:
    """对话数组记录 → 用户侧话轮拼接成单条 prompt（压测负载口径）。

    角色键全缺失或无人侧话轮时退化为拼接全部话轮；无话轮返回 None。
    """
    turns = _record_turns(record)
    if not turns:
        return None
    human = [text for text, role in turns if role in _HUMAN_ROLES]
    return "\n".join(human or [text for text, _ in turns]) or None
