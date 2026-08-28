"""Score judge: rubric 1-10 -> normalized 0~1; threshold judged on the 1-10 scale."""

from __future__ import annotations

from typing import ClassVar

from aite.core.interfaces import Judge
from aite.core.registry import register_judge
from aite.core.types import MetricResult
from aite.metrics.llm_based import DEFAULT_RUBRIC, parse_json_loose

SYSTEM_PROMPT = (
    "You are a strict, impartial evaluation judge. Assess the AI answer against "
    "the rubric. Output JSON only, no markdown fences, no extra keys."
)


@register_judge("score")
class ScoreJudge(Judge):
    name: ClassVar[str] = "score"

    def __init__(self, threshold: float | None = None, weight: float = 1.0,
                 rubric: str | None = None, model: str | None = None, **params) -> None:
        super().__init__(threshold=threshold, weight=weight, **params)
        self.rubric = rubric or DEFAULT_RUBRIC
        self.model_alias = model
        self.scale_max = float(self.params.get("scale_max", 10.0))
        # judge prompt version - persisted with results for reproducibility
        self.prompt_version = f"score-v1(rubric={_fingerprint(self.rubric)})"

    async def judge(self, *, case, response, baseline=None, ctx) -> MetricResult:
        if response.error:
            return MetricResult(metric=self.name, value=0.0, passed=False,
                                error=f"target error: {response.error[:200]}")
        reference = _reference_text(case)
        user = _build_user_prompt(self.rubric, case, response, reference)
        try:
            result = await ctx.llm_complete(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": user}],
                model=self.model_alias, temperature=0.0,
                max_tokens=int(self.params.get("max_tokens", 800)), json_mode=True,
            )
        except Exception as exc:  # noqa: BLE001
            return MetricResult(metric=self.name, value=0.0, error=f"judge call failed: {exc}")
        data = parse_json_loose(result)
        score = _extract_score(data)
        if score is None:
            return MetricResult(metric=self.name, value=0.0,
                                error=f"unparseable judge output: {result[:200]}")
        score = max(0.0, min(score, self.scale_max))
        value = score / self.scale_max
        passed = None
        if self.threshold is not None:      # threshold interpreted on raw scale (e.g. 7/10)
            passed = score >= self.threshold
        return MetricResult(
            metric=self.name, value=round(value, 6),
            raw={"score": score, "scale_max": self.scale_max,
                 "reasoning": (data or {}).get("reasoning", ""),
                 "prompt_version": self.prompt_version},
            passed=passed,
        )


def _extract_score(data: dict | None) -> float | None:
    if not data:
        return None
    for key in ("score", "rating", "points", "value"):
        if key in data:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                continue
    return None


def _reference_text(case) -> str:
    exp = case.expected
    if exp is None:
        return "(none provided)"
    if isinstance(exp, list):
        return "\n".join(f"- {e}" for e in exp)
    if isinstance(exp, dict):
        for key in ("answer", "gold", "reference", "output", "text"):
            if isinstance(exp.get(key), str):
                return exp[key]
        import json
        return json.dumps(exp, ensure_ascii=False)
    return str(exp)


def _build_user_prompt(rubric: str, case, response, reference: str) -> str:
    question = case.input if isinstance(case.input, str) else _stringify(case.input)
    return (
        f"## Rubric\n{rubric}\n\n"
        f"## Question / Task\n{question}\n\n"
        f"## Reference answer\n{reference}\n\n"
        f"## AI answer to evaluate\n{response.output_text}\n\n"
        'Respond with JSON: {"score": <number>, "reasoning": "<one sentence>"}'
    )


def _stringify(value) -> str:
    import json
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _fingerprint(rubric: str) -> str:
    import hashlib
    return hashlib.md5(rubric.encode("utf-8")).hexdigest()[:8]
