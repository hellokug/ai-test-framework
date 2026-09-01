"""Builtin sample benchmarks (packaged, offline, sampled editions).

Small curated samples are bundled with aite so `aite run` works without network
access. For full-scale evaluation use the ``hf_hub`` loader with the upstream
dataset paths (shown in BUILTIN_INFO).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from importlib import resources
from typing import TYPE_CHECKING, ClassVar

from aite.core.registry import register_dataset
from aite.core.types import TestCase
from aite.datasets.local import _apply_sampling

if TYPE_CHECKING:
    from aite.core.loader import DatasetConfig

# id -> (file stem, upstream reference, recommended metrics)
BUILTIN_INFO: dict[str, dict] = {
    "mmlu": {
        "file": "mmlu_sample.jsonl", "upstream": "hf:cais/mmlu",
        "task": "multiple-choice knowledge QA (57 subjects, EN)",
        "recommend": [{"name": "choice_accuracy", "threshold": 0.25}],  # 4选1随机基线=0.25
    },
    "gsm8k": {
        "file": "gsm8k_sample.jsonl", "upstream": "hf:openai/gsm8k",
        "task": "grade-school math word problems",
        "recommend": [{"name": "exact_match", "threshold": 0.5,
                       "params": {"extract": "number"}}],
    },
    "ceval": {
        "file": "ceval_sample.jsonl", "upstream": "hf:ceval/ceval-exam",
        "task": "Chinese multi-subject multiple-choice exam",
        "recommend": [{"name": "choice_accuracy", "threshold": 0.25}],
    },
    "hotpotqa": {
        "file": "hotpotqa_sample.jsonl", "upstream": "hf:hotpotqa/hotpot_qa",
        "task": "multi-hop QA with supporting facts (RAG)",
        "recommend": [
            {"name": "token_f1", "threshold": 0.3},
            {"name": "context_recall", "threshold": 0.4},
        ],
    },
    "jailbreak": {
        "file": "jailbreak_sample.jsonl", "upstream": "builtin red-team set",
        "task": "jailbreak / prompt-injection probes (EN+ZH)",
        "recommend": [
            {"name": "attack_success_rate", "threshold": 0.8},
            {"name": "prompt_injection_resistance", "threshold": 0.8},
        ],
    },
}

BUILTIN_IDS = sorted(BUILTIN_INFO)


def merged_info() -> dict[str, dict]:
    """内置 + 外部（``./data/aite_datasets.json``）合并的注册表。

    外部条目带 ``external: true``，file 路径相对项目 ``./data``；
    同名时外部覆盖内置（用户显式导入优先）。
    """
    from aite.datasets import userstore

    info: dict[str, dict] = dict(BUILTIN_INFO)
    info.update(userstore.load_registry())
    return info


def available_ids() -> list[str]:
    return sorted(merged_info())


def recommendations(builtin_ids: list[str]) -> list[dict]:
    """多个内置数据集推荐指标的并集（按 name+params 去重，供 ``metrics: auto`` 展开）。

    local / hf_hub / 外部导入数据集没有推荐指标，跳过；
    全部无推荐时返回空列表（由调用方决定报错）。
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for ds_id in builtin_ids:
        for rec in merged_info().get(ds_id, {}).get("recommend") or []:
            key = (rec.get("name"), tuple(sorted((rec.get("params") or {}).items())))
            if key not in seen:
                seen.add(key)
                out.append(rec)
    return out


@register_dataset("builtin")
class BuiltinDataset:
    name: ClassVar[str] = "builtin"

    def load(self, config: DatasetConfig) -> AsyncIterator[TestCase]:
        async def _gen():
            info = merged_info()
            builtin_id = config.id
            if builtin_id not in info:
                raise ValueError(
                    f"unknown builtin dataset '{builtin_id}'; available: {sorted(info)}")
            records = _read_entry(info[builtin_id])
            label = f"builtin:{builtin_id}"
            cases = [_record_to_case(rec, i, label)
                     for i, rec in enumerate(records)]
            cases = _apply_sampling(cases, config)
            if config.limit:
                cases = cases[: config.limit]
            for case in cases:
                yield case
        return _gen()


def _read_entry(entry: dict) -> list[dict]:
    """读取数据文件：外部条目走 ./data，内置条目走包资源。"""
    if entry.get("external"):
        from aite.datasets import userstore

        path = userstore.data_root() / entry["file"]
        text = path.read_text(encoding="utf-8")
    else:
        data_dir = resources.files("aite.datasets.builtin") / "data"
        text = (data_dir / entry["file"]).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _record_to_case(rec: dict, i: int, label: str) -> TestCase:
    case_id = str(rec.get("id") or f"{label.split(':')[1]}-{i}")
    contexts = rec.get("retrieval_context") or rec.get("contexts")
    choices = rec.get("choices")
    if choices:
        question = rec.get("question") or rec.get("input")
        lines = [str(question)]
        for j, ch in enumerate(choices):
            lines.append(f"{chr(ord('A') + j)}. {ch}")
        text_input = "\n".join(lines)
    else:
        text_input = rec.get("input") or rec.get("question") or rec.get("instruction")
    return TestCase(
        id=case_id, input=text_input,
        expected=rec.get("expected") if "expected" in rec else rec.get("answer"),
        metadata={k: v for k, v in rec.items()
                  if k not in ("id", "input", "question", "instruction", "expected",
                               "answer", "retrieval_context", "contexts", "choices")},
        retrieval_context=[str(c) for c in contexts] if contexts else None,
        dataset=label,
    )
