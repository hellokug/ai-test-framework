"""RAGAS 指标补遗（RAGAS 评测套件的组成约定见下）。

RAGAS 五件套在本框架的对应实现（口径对齐说明）：
- faithfulness            → aite.metrics.rag_metrics.Faithfulness（断言支撑，LLM/回退双模）
- answer_relevancy        → aite.metrics.embedding.AnswerRelevancy（question-answer 相似度）
- context_precision       → aite.metrics.rag_metrics.ContextPrecision（秩加权精确率）
- context_recall          → aite.metrics.rag_metrics.ContextRecall（断言归因）
- answer_correctness      → 本模块（事实重合 + 语义相似加权，RAGAS 缺省 0.75/0.25）

无 gateway 时各指标退化为确定性规则口径（宁缺毋错，error/raw.mode 标注来源）。
"""

from __future__ import annotations

from typing import ClassVar

from aite.core.interfaces import Metric
from aite.core.registry import register_metric
from aite.core.types import MetricResult
from aite.metrics.embedding import reference_text


@register_metric("answer_correctness")
class AnswerCorrectness(Metric):
    """答案正确性：事实重合（token F1）与语义相似（embedding 余弦）加权和。

    value = weight_factual · F1(answer, reference) + (1-weight_factual) · cos(...)
    params.weight_factual 缺省 0.75（RAGAS 缺省权重）；reference 取 case.expected
    的 answer/gold/output/text 字段或标量本身。
    """

    name: ClassVar[str] = "answer_correctness"
    needs_llm: ClassVar[bool] = True

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        ref = reference_text(case)
        if not ref or not response.ok:
            return MetricResult(metric=self.name, value=0.0,
                                error="needs reference answer (case.expected)" if not ref
                                else (response.error or "")[:200])
        from aite.utils.text import cosine_similarity, token_f1

        weight = float(self.params.get("weight_factual", 0.75))
        f1 = token_f1(response.output_text, ref)[2]
        vectors = await ctx.embed([response.output_text, ref])
        sim = max(cosine_similarity(vectors[0], vectors[1]), 0.0)
        value = weight * f1 + (1.0 - weight) * sim
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"factual_f1": round(f1, 4),
                                 "semantic_sim": round(sim, 4),
                                 "weight_factual": weight},
                            passed=self.judge_passed(value) if self.threshold is not None else None)
