"""Rule / NLP / performance metric unit tests."""

import pytest

from aite.core.types import (
    EvalContext,
    TargetResponse,
    TestCase,
    Timings,
    TokenUsage,
)
from aite.metrics.nlp_stats import Bleu, RougeL
from aite.metrics.performance import (
    ITL,
    ITLP95,
    TPOT,
    TPOTP99,
    TTFT,
    TTFTP99,
    CacheHitRate,
    ErrorRate,
    ITLMax,
    LatencyP95,
    TokensPerSec,
)
from aite.metrics.rules import (
    ChoiceAccuracy,
    Contains,
    ExactMatch,
    KeywordCoverage,
    NotContains,
    RegexMatch,
    TokenF1,
)

ctx = EvalContext(run_name="test")


def case(**kw):
    kw.setdefault("id", "c1")
    return TestCase(**kw)


def resp(text="", error=None, ms=100.0, ttft=None, usage=None, itl=None, raw=None):
    return TargetResponse(output=text, error=error, usage=usage, raw=raw,
                          timings=Timings(total_ms=ms, ttft_ms=ttft,
                                          itl_samples_ms=itl or []))


@pytest.mark.asyncio
async def test_exact_match_number_mode():
    m = ExactMatch(threshold=0.5, extract="number")
    c = case(input="q", expected="#### 72")
    ok = await m.compute(case=c, response=resp("#### 72"), ctx=ctx)
    assert ok.value == 1.0 and ok.passed is True
    bad = await m.compute(case=c, response=resp("#### 71"), ctx=ctx)
    assert bad.value == 0.0


@pytest.mark.asyncio
async def test_exact_match_no_reference_fails():
    m = ExactMatch()
    r = await m.compute(case=case(input="q"), response=resp("x"), ctx=ctx)
    assert r.value == 0.0


@pytest.mark.asyncio
async def test_choice_accuracy():
    m = ChoiceAccuracy()
    c = case(input="q", expected="B", metadata={"choices": ["a", "b", "c", "d"]})
    assert (await m.compute(case=c, response=resp("The answer is B."), ctx=ctx)).value == 1.0
    assert (await m.compute(case=c, response=resp("Answer: D"), ctx=ctx)).value == 0.0


@pytest.mark.asyncio
async def test_contains_and_not_contains():
    c = case(input="q", expected="Paris")
    assert (await Contains().compute(case=c, response=resp("It is Paris."), ctx=ctx)).value == 1.0
    assert (await NotContains().compute(case=c, response=resp("London"), ctx=ctx)).value == 1.0
    assert (await NotContains().compute(case=c, response=resp("Paris!"), ctx=ctx)).value == 0.0


@pytest.mark.asyncio
async def test_regex_match():
    m = RegexMatch(pattern=r"EV-\d{4}")
    assert (await m.compute(case=case(input="q"), response=resp("ticket EV-2024 ok"), ctx=ctx)).value == 1.0
    assert (await m.compute(case=case(input="q"), response=resp("no match"), ctx=ctx)).value == 0.0


@pytest.mark.asyncio
async def test_keyword_coverage():
    m = KeywordCoverage(threshold=0.5)
    c = case(input="q", expected={"keywords": ["退款", "30天"]})
    r = await m.compute(case=c, response=resp("30天内可申请退款"), ctx=ctx)
    assert r.value == 1.0 and r.passed


@pytest.mark.asyncio
async def test_token_f1():
    c = case(input="q", expected="the cat sat")
    r = await TokenF1().compute(case=c, response=resp("the cat sat down"), ctx=ctx)
    assert 0 < r.value < 1


@pytest.mark.asyncio
async def test_error_response_scores_zero():
    c = case(input="q", expected="x")
    r = await ExactMatch().compute(case=c, response=resp(error="HTTP 500"), ctx=ctx)
    assert r.value == 0.0


@pytest.mark.asyncio
async def test_rouge_and_bleu():
    c = case(input="q", expected="the cat sat on the mat")
    assert (await RougeL().compute(case=c, response=resp("the cat sat"), ctx=ctx)).value > 0.4
    assert (await Bleu().compute(case=c, response=resp("the cat sat on the mat"), ctx=ctx)).value > 0.9


@pytest.mark.asyncio
async def test_ttft_missing():
    r = await TTFT().compute(case=case(input="q"), response=resp(ttft=None), ctx=ctx)
    assert r.error and r.value is None    # 不可测 → None 聚合跳过（非 0）


@pytest.mark.asyncio
async def test_latency_threshold_lower_better():
    m = LatencyP95(threshold=200)
    r = await m.compute(case=case(input="q"), response=resp(ms=150), ctx=ctx)
    assert r.value == 150 and r.passed is True
    r2 = await m.compute(case=case(input="q"), response=resp(ms=500), ctx=ctx)
    assert r2.passed is False


@pytest.mark.asyncio
async def test_tpot_formula_and_tier():
    """TPOT = (端到端 − TTFT) / (tokens − 1)；子类只换聚合档位。"""
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5)
    response = resp(ms=1000.0, ttft=200.0, usage=usage)
    m = await TPOT().compute(case=case(input="q"), response=response, ctx=ctx)
    assert m.value == pytest.approx(200.0)   # (1000−200)/(5−1)
    assert TPOTP99.aggregation == "p99" and TPOTP99.name == "tpot_p99_ms"
    # usage 缺失时回退内容块数近似（流式 raw.chunks）
    r2 = await TPOT().compute(case=case(input="q"),
                              response=resp(ms=1000.0, ttft=200.0,
                                            raw={"chunks": 5}), ctx=ctx)
    assert r2.value == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_tpot_unavailable_without_streaming():
    r = await TPOT().compute(case=case(input="q"), response=resp(), ctx=ctx)
    assert r.error and r.value is None


@pytest.mark.asyncio
async def test_itl_tiers_from_samples():
    """ITL 每请求产出同档统计量：mean/p95/max；档级再聚合换 aggregation。"""
    response = resp(ms=100.0, ttft=10.0, itl=[10.0, 20.0, 30.0, 40.0])
    mean = await ITL().compute(case=case(input="q"), response=response, ctx=ctx)
    assert mean.value == pytest.approx(25.0)
    p95 = await ITLP95().compute(case=case(input="q"), response=response, ctx=ctx)
    assert p95.value == pytest.approx(38.5)   # 线性插值 30×0.15 + 40×0.85
    mx = await ITLMax().compute(case=case(input="q"), response=response, ctx=ctx)
    assert mx.value == pytest.approx(40.0)
    empty = await ITL().compute(case=case(input="q"),
                                response=resp(ttft=10.0), ctx=ctx)
    assert empty.error and empty.value is None


@pytest.mark.asyncio
async def test_cache_hit_rate():
    m = CacheHitRate(threshold=0.2)
    usage = TokenUsage(prompt_tokens=1000, completion_tokens=5, cached_tokens=250)
    r = await m.compute(case=case(input="q"),
                        response=resp(usage=usage), ctx=ctx)
    assert r.value == pytest.approx(0.25) and r.passed is True
    assert r.raw == {"cached_tokens": 250, "prompt_tokens": 1000}
    # 端点未回传 cached_tokens → 不可测（None），不算误导性的 0
    miss = await m.compute(
        case=case(input="q"),
        response=resp(usage=TokenUsage(prompt_tokens=10, completion_tokens=5)),
        ctx=ctx)
    assert miss.error and miss.value is None


@pytest.mark.asyncio
async def test_ttft_tier_class_attrs():
    assert TTFTP99.name == "ttft_p99_ms" and TTFTP99.aggregation == "p99"


@pytest.mark.asyncio
async def test_error_rate():
    m = ErrorRate(threshold=0.1)
    assert (await m.compute(case=case(input="q"), response=resp(), ctx=ctx)).value == 0.0
    r = await m.compute(case=case(input="q"), response=resp(error="x"), ctx=ctx)
    assert r.value == 1.0 and r.passed is False


@pytest.mark.asyncio
async def test_tokens_per_sec_normalized():
    m = TokensPerSec()
    r = await m.compute(case=case(input="q"),
                        response=TargetResponse(output="x", timings=Timings(
                            total_ms=1000, ttft_ms=100, tokens_per_sec=250.0)), ctx=ctx)
    assert r.value == 0.5
