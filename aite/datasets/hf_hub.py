"""Hugging Face Hub dataset loader (requires aite[bench])."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, ClassVar

from aite.core.registry import register_dataset
from aite.core.types import TestCase
from aite.datasets.local import _apply_sampling, _as_context

if TYPE_CHECKING:
    from aite.core.loader import DatasetConfig

try:
    from datasets import load_dataset  # type: ignore
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "hf_hub dataset requires the [bench] extra: pip install 'aite[bench]'"
    ) from _exc

_INPUT_KEYS = ("input", "question", "instruction", "prompt", "query")
_EXPECTED_KEYS = ("expected", "answer", "gold", "output", "label", "target", "responses")
_CONTEXT_KEYS = ("retrieval_context", "contexts", "context", "documents", "supporting_facts")


@register_dataset("hf_hub")
class HFHubDataset:
    name: ClassVar[str] = "hf_hub"

    def load(self, config: DatasetConfig) -> AsyncIterator[TestCase]:
        async def _gen():
            path = config.id or config.path
            if not path:
                raise ValueError("hf_hub dataset requires id (dataset path)")
            split = config.split or "test"
            kwargs = dict(config.kwargs or {})
            ds = load_dataset(path, config.kwargs.get("name"), split=split, **{
                k: v for k, v in kwargs.items() if k != "name"})
            label = f"hf:{path}@{split}"
            cases = [self._to_case(dict(rec), config, label, i)
                     for i, rec in enumerate(ds)]
            cases = _apply_sampling(cases, config)
            if config.limit:
                cases = cases[: config.limit]
            for case in cases:
                yield case
        return _gen()

    def _to_case(self, rec: dict, config: DatasetConfig, label: str, i: int) -> TestCase:
        mapping = config.fields or {}

        def pick(keys: tuple[str, ...], mapped: str | None) -> tuple[str | None, Any]:
            if mapped and mapped in rec:
                return mapped, rec[mapped]
            for k in keys:
                if k in rec:
                    return k, rec[k]
            return None, None

        input_key, raw_input = pick(_INPUT_KEYS, mapping.get("input"))
        if input_key is None and mapping.get("input"):
            raise ValueError(f"field '{mapping['input']}' not in dataset columns: {list(rec)}")
        expected_key, raw_expected = pick(_EXPECTED_KEYS, mapping.get("expected"))
        context_key, raw_context = pick(_CONTEXT_KEYS, mapping.get("retrieval_context"))
        # MCQ benchmarks: choices + answer index
        if "choices" in rec:
            raw_input = _format_mcq(raw_input, rec)
            if raw_expected is None or isinstance(raw_expected, int):
                idx = raw_expected if isinstance(raw_expected, int) else 0
                letters = "ABCDEFGH"
                raw_expected = letters[idx] if 0 <= idx < len(letters) else str(idx)
        case_id = str(rec.get("id") or rec.get("case_id") or f"{label.split('@')[0]}-{i}")
        metadata = {k: _jsonable(v) for k, v in rec.items()
                    if k not in (input_key, expected_key, context_key, "id", "case_id")}
        return TestCase(
            id=case_id, input=raw_input if raw_input is not None else "",
            expected=raw_expected if raw_expected is not None else None,
            metadata=metadata,
            retrieval_context=_as_context(raw_context) if context_key else None,
            dataset=label,
        )


def _format_mcq(question: Any, rec: dict) -> str:
    choices = rec.get("choices") or []
    lines = [str(question)]
    for i, ch in enumerate(choices):
        lines.append(f"{chr(ord('A') + i)}. {ch}")
    return "\n".join(lines)


def _jsonable(value: Any) -> Any:
    import json
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)
