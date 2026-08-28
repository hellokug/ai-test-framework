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

# id -> (file stem, upstream reference)
BUILTIN_INFO: dict[str, dict] = {
    "mmlu": {
        "file": "mmlu_sample.jsonl", "upstream": "hf:cais/mmlu",
        "task": "multiple-choice knowledge QA (57 subjects, EN)",
        "metrics": ["choice_accuracy"],
    },
    "gsm8k": {
        "file": "gsm8k_sample.jsonl", "upstream": "hf:openai/gsm8k",
        "task": "grade-school math word problems",
        "metrics": ["exact_match (extract: number)"],
    },
    "ceval": {
        "file": "ceval_sample.jsonl", "upstream": "hf:ceval/ceval-exam",
        "task": "Chinese multi-subject multiple-choice exam",
        "metrics": ["choice_accuracy"],
    },
    "hotpotqa": {
        "file": "hotpotqa_sample.jsonl", "upstream": "hf:hotpotqa/hotpot_qa",
        "task": "multi-hop QA with supporting facts (RAG)",
        "metrics": ["token_f1", "context_recall", "faithfulness"],
    },
    "jailbreak": {
        "file": "jailbreak_sample.jsonl", "upstream": "builtin red-team set",
        "task": "jailbreak / prompt-injection probes (EN+ZH)",
        "metrics": ["attack_success_rate", "prompt_injection_resistance"],
    },
}

BUILTIN_IDS = sorted(BUILTIN_INFO)


@register_dataset("builtin")
class BuiltinDataset:
    name: ClassVar[str] = "builtin"

    def load(self, config: DatasetConfig) -> AsyncIterator[TestCase]:
        async def _gen():
            builtin_id = config.id
            if builtin_id not in BUILTIN_INFO:
                raise ValueError(
                    f"unknown builtin dataset '{builtin_id}'; available: {BUILTIN_IDS}")
            records = _read_builtin(BUILTIN_INFO[builtin_id]["file"])
            label = f"builtin:{builtin_id}"
            cases = [_record_to_case(rec, i, label)
                     for i, rec in enumerate(records)]
            cases = _apply_sampling(cases, config)
            if config.limit:
                cases = cases[: config.limit]
            for case in cases:
                yield case
        return _gen()


def _read_builtin(filename: str) -> list[dict]:
    data_dir = resources.files("aite.datasets.builtin") / "data"
    text = (data_dir / filename).read_text(encoding="utf-8")
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
