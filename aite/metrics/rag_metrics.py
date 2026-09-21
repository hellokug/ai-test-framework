"""RAG metrics.

Context sources (three, first wins):
1. ``case.retrieval_context`` - dataset-provided gold/actual contexts
2. ``response.raw["retrieval_context"]`` - RAGHookMixin / HTTP agent conventions
3. ``case.metadata["contexts"]`` - arbitrary metadata
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from aite.core.interfaces import Metric
from aite.core.registry import register_metric
from aite.core.types import MetricResult, TargetResponse, TestCase
from aite.metrics.llm_based import LLMMetricMixin
from aite.utils.text import tokenize

_CITATION_RE = re.compile(r"\[(\d+)\]")


def contexts_of(case: TestCase, response: TargetResponse) -> list[str]:
    if case.retrieval_context:
        return list(case.retrieval_context)
    raw_ctx = (response.raw or {}).get("retrieval_context")
    if isinstance(raw_ctx, list) and raw_ctx:
        return [str(c) for c in raw_ctx]
    meta_ctx = case.metadata.get("contexts")
    if isinstance(meta_ctx, list) and meta_ctx:
        return [str(c) for c in meta_ctx]
    return []


def _facts_of(case: TestCase) -> list[str]:
    exp = case.expected
    if isinstance(exp, dict):
        facts = exp.get("facts") or exp.get("gold_facts")
        if isinstance(facts, list):
            return [str(f) for f in facts]
        if isinstance(exp.get("answer"), str):
            return [exp["answer"]]
    if isinstance(exp, str):
        return [exp]
    if isinstance(exp, list):
        return [str(e) for e in exp]
    return []


@register_metric("context_recall")
class ContextRecall(Metric, LLMMetricMixin):
    """Gold facts covered by retrieved contexts (RAGAS 口径：断言级归因).

    LLM 模式（gateway 可用）逐条断言判定「能否归因到上下文」；规则回退
    token 覆盖率（fact 中 token 被上下文覆盖比例 ≥ coverage 记命中）。
    """

    name: ClassVar[str] = "context_recall"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        contexts, facts = contexts_of(case, response), _facts_of(case)
        if not facts or not contexts:
            return MetricResult(metric=self.name, value=0.0,
                                error="needs case.expected facts + retrieval contexts")
        if ctx.gateway is not None and not self.params.get("rule_only"):
            return await self._llm_mode(case, facts, contexts, ctx)
        ctx_toks = set(tokenize(" ".join(contexts)))
        covered = 0
        detail = []
        for fact in facts:
            toks = tokenize(fact)
            if not toks:
                continue
            hit = sum(1 for t in toks if t in ctx_toks) / len(toks)
            if hit >= float(self.params.get("coverage", 0.5)):
                covered += 1
            detail.append(round(hit, 4))
        value = covered / len(facts)
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"mode": "token_coverage", "facts": len(facts),
                                 "covered": covered, "per_fact": detail},
                            passed=self.judge_passed(value) if self.threshold is not None else None)

    async def _llm_mode(self, case, facts: list[str], contexts: list[str], ctx) -> MetricResult:
        joined = "\n---\n".join(contexts)[:6000]
        user = (
            "## Reference facts\n" + "\n".join(f"- {f}" for f in facts)
            + f"\n\n## Contexts\n{joined}\n\n"
            "Can each fact be attributed to (directly inferred from) the contexts? "
            'JSON only: {"claims": n, "attributed": n, "unattributed": ["fact", ...]}'
        )
        try:
            data = await self.llm_json(ctx, "You audit RAG retrieval coverage.",
                                       user, model=self.params.get("model"))
        except Exception as exc:  # noqa: BLE001
            return MetricResult(metric=self.name, value=0.0, error=f"llm failed: {exc}")
        if not data:
            return MetricResult(metric=self.name, value=0.0, error="unparseable judge output")
        claims = _as_int(data.get("claims"))
        attributed = _as_int(data.get("attributed"))
        value = attributed / claims if claims else 0.0
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"mode": "llm", "claims": claims,
                                 "attributed": attributed,
                                 "unattributed": data.get("unattributed", [])[:10]},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("context_precision")
class ContextPrecision(Metric, LLMMetricMixin):
    """RAGAS 口径：秩加权平均精确率（位置越靠前的相关上下文贡献越大）。

    value = (1/|relevant|) · Σ_k precision@k · relevant_k，无相关条目 = 0。
    LLM 模式（gateway 可用）逐条判定相关性；规则回退 token_f1 ≥ relevant_sim。
    """

    name: ClassVar[str] = "context_precision"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        contexts = contexts_of(case, response)
        question = case.input if isinstance(case.input, str) else str(case.input)
        if not contexts or not question:
            return MetricResult(metric=self.name, value=0.0,
                                error="needs question + retrieval contexts")
        if ctx.gateway is not None and not self.params.get("rule_only"):
            return await self._llm_mode(case, question, contexts, ctx)
        from aite.utils.text import token_f1

        sims = [token_f1(question, c)[2] for c in contexts]
        threshold = float(self.params.get("relevant_sim", 0.15))
        value = _rank_weighted_precision([s >= threshold for s in sims])
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"mode": "token_overlap", "contexts": len(contexts),
                                 "sims": [round(s, 4) for s in sims]},
                            passed=self.judge_passed(value) if self.threshold is not None else None)

    async def _llm_mode(self, case, question: str, contexts: list[str], ctx) -> MetricResult:
        numbered = "\n".join(f"[{i}] {c[:800]}" for i, c in enumerate(contexts, 1))
        user = (
            f"## Question\n{question}\n\n## Retrieved contexts\n{numbered}\n\n"
            "Is each context relevant (useful for answering the question)? "
            'JSON only: {"relevant": [true|false, ...]}  # one per context, in order'
        )
        try:
            data = await self.llm_json(ctx, "You audit RAG retrieval quality.",
                                       user, model=self.params.get("model"))
        except Exception as exc:  # noqa: BLE001
            return MetricResult(metric=self.name, value=0.0, error=f"llm failed: {exc}")
        if not data or not isinstance(data.get("relevant"), list):
            return MetricResult(metric=self.name, value=0.0, error="unparseable judge output")
        verdicts = [bool(v) for v in list(data["relevant"])[:len(contexts)]]
        verdicts += [False] * (len(contexts) - len(verdicts))
        value = _rank_weighted_precision(verdicts)
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"mode": "llm", "relevant": verdicts},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


def _rank_weighted_precision(relevant: list[bool]) -> float:
    """RAGAS context_precision 公式：Σ precision@k·rel_k / |relevant|。"""
    hits, weighted = 0, 0.0
    for k, rel in enumerate(relevant, 1):
        if rel:
            hits += 1
            weighted += hits / k
    return weighted / hits if hits else 0.0


def _as_int(value: Any, default: int = 0) -> int:
    """judge 回传的计数字段可能是字符串/None——宽松取整，坏值回退 0（最差分）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@register_metric("hit_rate")
class HitRate(Metric):
    """hit@k: does the retrieved context contain the gold doc/snippet id or text?"""

    name: ClassVar[str] = "hit_rate"
    aggregation: ClassVar[str] = "rate"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        contexts = contexts_of(case, response)
        k = int(self.params.get("k", len(contexts) or 1))
        gold = case.metadata.get("gold_doc")
        if isinstance(case.expected, dict):
            gold = gold or case.expected.get("gold_doc")
        if gold is None:
            gold = _facts_of(case)[0] if _facts_of(case) else None
        if gold is None or not contexts:
            return MetricResult(metric=self.name, value=0.0,
                                error="needs gold doc + retrieval contexts")
        top = contexts[:k]
        gold_str = str(gold)
        hit = any(gold_str.lower() in c.lower() or c.lower() in gold_str.lower() for c in top)
        value = 1.0 if hit else 0.0
        return MetricResult(metric=self.name, value=value, raw={"k": k, "hit": hit},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("faithfulness")
class Faithfulness(Metric, LLMMetricMixin):
    """Answer grounded in contexts? LLM mode (claim support) with token-overlap fallback."""

    name: ClassVar[str] = "faithfulness"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        contexts = contexts_of(case, response)
        answer = response.output_text
        if not contexts or not answer or not response.ok:
            return MetricResult(metric=self.name, value=0.0,
                                error="needs retrieval contexts + answer")
        if ctx.gateway is not None and not self.params.get("rule_only"):
            return await self._llm_mode(case, answer, contexts, ctx)
        # fallback: unsupported-token ratio (answer tokens not present in contexts)
        answer_toks = tokenize(answer)
        ctx_toks = set(tokenize(" ".join(contexts)))
        if not answer_toks:
            return MetricResult(metric=self.name, value=0.0)
        supported = sum(1 for t in answer_toks if t in ctx_toks)
        value = supported / len(answer_toks)
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"mode": "token_overlap", "tokens": len(answer_toks)},
                            passed=self.judge_passed(value) if self.threshold is not None else None)

    async def _llm_mode(self, case, answer: str, contexts: list[str], ctx) -> MetricResult:
        joined = "\n---\n".join(contexts)[:6000]
        user = (
            f"## Contexts\n{joined}\n\n## Answer\n{answer}\n\n"
            "Decompose the answer into factual claims and check each against the "
            'contexts. JSON only: {"claims": n, "supported": n, '
            '"unsupported": ["claim", ...]}'
        )
        try:
            data = await self.llm_json(ctx, "You audit RAG answers for hallucinations.",
                                       user, model=self.params.get("model"))
        except Exception as exc:  # noqa: BLE001
            return MetricResult(metric=self.name, value=0.0, error=f"llm failed: {exc}")
        if not data:
            return MetricResult(metric=self.name, value=0.0, error="unparseable judge output")
        claims = _as_int(data.get("claims"))
        supported = _as_int(data.get("supported"))
        value = supported / claims if claims else 0.0
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"mode": "llm", "claims": claims, "supported": supported,
                                 "unsupported": data.get("unsupported", [])[:10]},
                            passed=self.judge_passed(value) if self.threshold is not None else None)


@register_metric("citation_accuracy")
class CitationAccuracy(Metric):
    """[n] citations point to contexts that actually support the cited statement."""

    name: ClassVar[str] = "citation_accuracy"

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        contexts = contexts_of(case, response)
        answer = response.output_text
        cites = _CITATION_RE.findall(answer)
        if not cites:
            return MetricResult(metric=self.name, value=0.0,
                                error="no [n] citations found in answer")
        valid = sum(1 for n in cites if 1 <= int(n) <= len(contexts))
        value = valid / len(cites)
        return MetricResult(metric=self.name, value=round(value, 6),
                            raw={"citations": len(cites), "valid": valid,
                                 "contexts": len(contexts)},
                            passed=self.judge_passed(value) if self.threshold is not None else None)

