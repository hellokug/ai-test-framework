"""Statistical NLP metrics: BLEU / ROUGE-L / chrF-lite / similarity stats."""

from __future__ import annotations

from typing import ClassVar

from aite.core.interfaces import Metric
from aite.core.registry import register_metric
from aite.core.types import MetricResult
from aite.utils.text import bleu, rouge_l, tokenize


def _references(case) -> list[str]:
    exp = case.expected
    if exp is None:
        return []
    if isinstance(exp, list):
        return [str(e) for e in exp]
    if isinstance(exp, dict):
        if isinstance(exp.get("references"), list):
            return [str(r) for r in exp["references"]]
        for key in ("answer", "gold", "output", "text"):
            if isinstance(exp.get(key), str):
                return [exp[key]]
    return [str(exp)]


@register_metric("bleu")
class Bleu(Metric):
    name: ClassVar[str] = "bleu"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        refs = _references(case)
        if not refs or not response.ok:
            return MetricResult(metric=self.name, value=0.0)
        try:
            from sacrebleu import sentence_bleu  # type: ignore

            value = sentence_bleu(response.output_text, refs).score / 100.0
            source = "sacrebleu"
        except ImportError:
            value = bleu(response.output_text, refs)
            source = "builtin"
        return MetricResult(
            metric=self.name, value=round(value, 6), raw={"engine": source},
            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("rouge_l")
class RougeL(Metric):
    name: ClassVar[str] = "rouge_l"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        refs = _references(case)
        if not refs or not response.ok:
            return MetricResult(metric=self.name, value=0.0)
        try:
            from rouge_score import rouge_scorer  # type: ignore

            scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
            best = max(scorer.score(ref, response.output_text)["rougeL"].fmeasure
                       for ref in refs)
            source = "rouge-score"
        except ImportError:
            best = max(rouge_l(response.output_text, ref)[2] for ref in refs)
            source = "builtin"
        return MetricResult(
            metric=self.name, value=round(best, 6), raw={"engine": source},
            passed=self.judge_passed(best) if self.threshold is not None else None)


@register_metric("length_ratio")
class LengthRatio(Metric):
    """len(output)/len(reference) clipped to 0~1 via min(1, 1/x + x)/2 style score."""

    name: ClassVar[str] = "length_ratio"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        refs = _references(case)
        if not refs:
            return MetricResult(metric=self.name, value=0.0, error="no reference")
        ref_len = min(len(r) for r in refs)
        out_len = len(response.output_text)
        if ref_len == 0:
            value = 1.0 if out_len == 0 else 0.0
        else:
            ratio = out_len / ref_len
            value = min(ratio, 1 / ratio) if ratio > 0 else 0.0
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"output_len": out_len, "ref_len": ref_len},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("distinct_n")
class DistinctN(Metric):
    """Distinct-n gram ratio - diversity / repetition detector (higher = less repetitive)."""

    name: ClassVar[str] = "distinct_n"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        n = int(self.params.get("n", 2))
        toks = tokenize(response.output_text)
        if len(toks) < n:
            return MetricResult(metric=self.name, value=0.0, raw={"tokens": len(toks)})
        grams = [tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)]
        value = len(set(grams)) / len(grams)
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"n": n, "grams": len(grams)},
                            passed=self.judge_passed(value) if self.threshold is not None else None)
