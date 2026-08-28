"""Rule-based functional metrics: exact match / contains / regex / choice / F1 ..."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar

from aite.core.interfaces import Metric
from aite.core.registry import register_metric
from aite.core.types import MetricResult
from aite.utils.text import exact_match, extract_choice_letter

if TYPE_CHECKING:
    from aite.core.types import TestCase


def _expected_texts(case: TestCase) -> list[str]:
    """Normalize ``case.expected`` into candidate reference strings."""
    exp = case.expected
    if exp is None:
        return []
    if isinstance(exp, list):
        return [str(e) for e in exp]
    if isinstance(exp, dict):
        for key in ("answer", "answers", "gold", "output", "text"):
            if key in exp:
                return _expected_texts_from(exp[key])
        return [str(exp)]
    return [str(exp)]


def _expected_texts_from(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _options(case: TestCase, metric: Metric) -> dict[str, Any]:
    """Params may come from metric config or per-case expected dict."""
    merged = dict(metric.params)
    if isinstance(case.expected, dict):
        merged.update({k: v for k, v in case.expected.items() if k in (
            "extract", "tolerance", "case_sensitive", "regex", "keywords",
            "min_length", "max_length", "choices",
        )})
    return merged


@register_metric("exact_match")
class ExactMatch(Metric):
    """Exact match with modes: plain / extract=number (GSM8K) / extract=choice (MMLU)."""

    name: ClassVar[str] = "exact_match"
    aggregation: ClassVar[str] = "rate"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        refs = _expected_texts(case)
        opts = _options(case, self)
        ok = bool(refs) and response.ok and exact_match(
            response.output_text, refs,
            extract=opts.get("extract"),
            tol=float(opts["tolerance"]) if opts.get("tolerance") is not None else None,
            case_sensitive=bool(opts.get("case_sensitive", False)),
        )
        value = 1.0 if ok else 0.0
        return MetricResult(
            metric=self.name, value=value, raw={"prediction": response.output_text[:500],
                                                "references": refs[:5]},
            passed=self.judge_passed(value) if self.threshold is not None else None,
        )


@register_metric("accuracy")
class Accuracy(ExactMatch):
    """Alias of exact_match for benchmark parity (acc)."""
    name: ClassVar[str] = "accuracy"
    aggregation: ClassVar[str] = "rate"


@register_metric("choice_accuracy")
class ChoiceAccuracy(Metric):
    """Multiple-choice letter match (A-H); letter-match mode."""

    name: ClassVar[str] = "choice_accuracy"
    aggregation: ClassVar[str] = "rate"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        refs = _expected_texts(case)
        choices_len = len(case.metadata.get("choices", [])) or 4
        pred = extract_choice_letter(response.output_text, choices_len)
        gold = None
        for ref in refs:
            letter = extract_choice_letter(ref, choices_len)
            gold = letter or (ref.strip()[:1].upper() if ref.strip() else None)
            if gold:
                break
        ok = pred is not None and pred == gold
        value = 1.0 if ok else 0.0
        return MetricResult(
            metric=self.name, value=value,
            raw={"predicted": pred, "gold": gold, "excerpt": response.output_text[:300]},
            passed=self.judge_passed(value) if self.threshold is not None else None,
        )


@register_metric("contains")
class Contains(Metric):
    name: ClassVar[str] = "contains"
    aggregation: ClassVar[str] = "rate"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        refs = _expected_texts(case) or [str(self.params.get("text", ""))]
        case_sensitive = bool(self.params.get("case_sensitive", False))
        haystack = response.output_text if case_sensitive else response.output_text.lower()
        ok = response.ok and any(
            (r if case_sensitive else r.lower()) in haystack for r in refs if r)
        value = 1.0 if ok else 0.0
        return MetricResult(metric=self.name, value=value, raw={"references": refs[:5]},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("not_contains")
class NotContains(Metric):
    """Output must NOT contain any of the forbidden strings."""

    name: ClassVar[str] = "not_contains"
    aggregation: ClassVar[str] = "rate"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        forbidden = _expected_texts(case) or [str(v) for v in (self.params.get("texts") or [])]
        haystack = response.output_text.lower()
        hit = next((f for f in forbidden if f.lower() in haystack), None)
        value = 0.0 if hit else 1.0
        return MetricResult(metric=self.name, value=value, raw={"violated": hit},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("regex_match")
class RegexMatch(Metric):
    name: ClassVar[str] = "regex_match"
    aggregation: ClassVar[str] = "rate"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        pattern = self.params.get("pattern") or (
            case.expected.get("regex") if isinstance(case.expected, dict) else None)
        if not pattern:
            return MetricResult(metric=self.name, value=0.0, error="no regex pattern configured")
        flags = 0 if self.params.get("case_sensitive") else re.IGNORECASE
        ok = response.ok and re.search(pattern, response.output_text, flags) is not None
        value = 1.0 if ok else 0.0
        return MetricResult(metric=self.name, value=value, raw={"pattern": pattern},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("keyword_coverage")
class KeywordCoverage(Metric):
    """Fraction of expected keywords present in the output."""

    name: ClassVar[str] = "keyword_coverage"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        keywords: list[str] = []
        if isinstance(case.expected, dict) and isinstance(case.expected.get("keywords"), list):
            keywords = [str(k) for k in case.expected["keywords"]]
        keywords = keywords or [str(k) for k in (self.params.get("keywords") or [])]
        if not keywords:
            return MetricResult(metric=self.name, value=0.0, error="no keywords configured")
        text = response.output_text.lower()
        hits = [k for k in keywords if k.lower() in text]
        value = len(hits) / len(keywords)
        return MetricResult(
            metric=self.name, value=round(value, 6),
            raw={"hits": hits, "misses": [k for k in keywords if k not in hits]},
            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("length_between")
class LengthBetween(Metric):
    """Output length (chars) within [min, max] -> 1 else graded 0..1."""

    name: ClassVar[str] = "length_between"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        opts = self.params
        lo = int(opts.get("min", 0))
        hi = int(opts.get("max", 10**9))
        n = len(response.output_text)
        if lo <= n <= hi:
            value = 1.0
        elif n < lo:
            value = n / max(lo, 1)
        else:
            value = max(0.0, 1 - (n - hi) / max(hi, 1))
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"length": n, "min": lo, "max": hi},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("token_f1")
class TokenF1(Metric):
    """SQuAD-style token F1 (max over multiple references)."""

    name: ClassVar[str] = "token_f1"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        from aite.utils.text import token_f1_multi

        refs = _expected_texts(case)
        if not refs or not response.ok:
            return MetricResult(metric=self.name, value=0.0,
                                raw={"references": refs[:3]},
                                passed=self.judge_passed(0.0) if self.threshold is not None else None)
        precision, recall, f1 = token_f1_multi(response.output_text, refs)
        return MetricResult(
            metric=self.name, value=round(f1, 6),
            raw={"precision": round(precision, 6), "recall": round(recall, 6)},
            passed=self.judge_passed(f1) if self.threshold is not None else None)


@register_metric("instruction_following")
class InstructionFollowing(Metric):
    """Cheap proxy checks for instruction compliance (keywords present/absent,
    length bounds) driven by ``case.metadata.constraints``::

        constraints:
          must_include: ["方案", "报价"]
          must_exclude: ["抱歉"]
          min_chars: 50
    """

    name: ClassVar[str] = "instruction_following"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        cons: dict[str, Any] = dict(case.metadata.get("constraints") or {})
        cons.update(self.params)
        checks: dict[str, bool] = {}
        text = response.output_text
        for kw in cons.get("must_include", []):
            checks[f"include:{kw}"] = str(kw).lower() in text.lower()
        for kw in cons.get("must_exclude", []):
            checks[f"exclude:{kw}"] = str(kw).lower() not in text.lower()
        if cons.get("min_chars"):
            checks["min_chars"] = len(text) >= int(cons["min_chars"])
        if cons.get("max_chars"):
            checks["max_chars"] = len(text) <= int(cons["max_chars"])
        if cons.get("lang"):
            checks["lang"] = _lang_of(text) == cons["lang"]
        if not checks:
            return MetricResult(metric=self.name, value=0.0,
                                error="no constraints found (metadata.constraints)")
        value = sum(1 for ok in checks.values() if ok) / len(checks)
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"checks": checks},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


def _lang_of(text: str) -> str:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return "zh" if cjk > len(text) * 0.15 else "en"


@register_metric("consistency")
class Consistency(Metric):
    """Self-consistency over repeat variants: normalized-equality of each repeat
    against the majority/base answer (requires dataset ``repeats: N``)."""

    name: ClassVar[str] = "consistency"
    wants_variants: ClassVar[bool] = True

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        if not responses:
            return MetricResult(metric=self.name, value=0.0, error="no variant responses")
        from aite.utils.text import normalize_answer

        answers = [normalize_answer(r.output_text) for _, r in responses]
        answers = [a for a in answers if a]
        if not answers:
            return MetricResult(metric=self.name, value=0.0)
        counts: dict[str, int] = {}
        for a in answers:
            counts[a] = counts.get(a, 0) + 1
        majority, n = max(counts.items(), key=lambda kv: kv[1])
        value = n / len(answers)
        return MetricResult(
            metric=self.name, value=round(value, 6),
            raw={"majority": majority[:200], "distinct": len(counts), "n": len(answers)},
            passed=self.judge_passed(value) if self.threshold is not None else None,
        )
