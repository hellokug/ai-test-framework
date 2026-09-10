"""Embedding-similarity metrics (sentence-transformers when installed, else
deterministic hash embeddings)."""

from __future__ import annotations

from typing import ClassVar

from aite.core.interfaces import Metric
from aite.core.registry import register_metric
from aite.core.types import MetricResult
from aite.utils.text import cosine_similarity


def _reference_text(case) -> str:
    exp = case.expected
    if exp is None:
        return ""
    if isinstance(exp, list):
        return str(exp[0])
    if isinstance(exp, dict):
        for key in ("answer", "gold", "output", "text"):
            if isinstance(exp.get(key), str):
                return exp[key]
    return str(exp)


@register_metric("embedding_similarity")
class EmbeddingSimilarity(Metric):
    """Cosine similarity between output and expected answer embeddings."""
    needs_llm: ClassVar[bool] = True

    name: ClassVar[str] = "embedding_similarity"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        ref = _reference_text(case)
        if not ref or not response.ok:
            return MetricResult(metric=self.name, value=0.0, raw={"reference": ref[:200]})
        vectors = await ctx.embed([response.output_text, ref])
        sim = cosine_similarity(vectors[0], vectors[1])
        return MetricResult(metric=self.name, value=round(max(sim, 0.0), 6),
                            raw={"reference": ref[:200]},
                            passed=self.judge_passed(sim) if self.threshold is not None else None)


@register_metric("answer_relevancy")
class AnswerRelevancy(Metric):
    """RAG metric: cosine(question, answer) - does the answer address the question?"""
    needs_llm: ClassVar[bool] = True

    name: ClassVar[str] = "answer_relevancy"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        question = case.input if isinstance(case.input, str) else str(case.input)
        if not response.ok or not question:
            return MetricResult(metric=self.name, value=0.0)
        vectors = await ctx.embed([question, response.output_text])
        sim = cosine_similarity(vectors[0], vectors[1])
        return MetricResult(metric=self.name, value=round(max(sim, 0.0), 6),
                            raw={},
                            passed=self.judge_passed(sim) if self.threshold is not None else None)
