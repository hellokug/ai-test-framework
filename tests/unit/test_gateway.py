"""Gateway tests: cost accounting, budget breaker, cache."""


import httpx
import pytest

from aite.core.loader import TargetConfig
from aite.core.types import TokenUsage
from aite.gateway.llm_gateway import CostLimitExceeded, LLMGateway


@pytest.mark.asyncio
async def test_register_and_complete_openai(monkeypatch):
    gw = LLMGateway()
    gw.register_model(TargetConfig(type="openai_compatible", name="j1",
                                   base_url="http://mock/v1", model="gpt-4o-mini"))
    captured: dict = {}

    async def fake_post(self, url, json=None, **kw):
        captured["url"] = str(url)
        captured["body"] = json
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"score": 7}'},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await gw.complete([{"role": "user", "content": "hi"}], model="j1",
                               cache=False)
    await gw.close()
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "gpt-4o-mini"
    assert captured["body"]["temperature"] == 0.0
    assert result.text == '{"score": 7}'
    assert result.usage.total_tokens == 15
    # gpt-4o-mini pricing: (10*0.15 + 5*0.60)/1e6
    assert result.cost_usd == pytest.approx((10 * 0.15 + 5 * 0.60) / 1e6, rel=1e-6)
    assert gw.total_cost_usd == pytest.approx(result.cost_usd)


@pytest.mark.asyncio
async def test_cache_hit_avoids_second_call(monkeypatch, tmp_path):
    monkeypatch.setenv("AITE_CACHE_DIR", str(tmp_path / "cache"))
    gw = LLMGateway()
    gw.register_model(TargetConfig(type="openai_compatible", name="j1",
                                   base_url="http://mock/v1", model="mock"))
    calls = {"n": 0}

    async def fake_post(self, url, json=None, **kw):
        calls["n"] += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "same"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    r1 = await gw.complete([{"role": "user", "content": "cached?"}], model="j1")
    r2 = await gw.complete([{"role": "user", "content": "cached?"}], model="j1")
    await gw.close()
    assert calls["n"] == 1 and r2.cached and r2.text == r1.text


@pytest.mark.asyncio
async def test_budget_breaker():
    gw = LLMGateway(max_cost_usd=0.0)
    gw.register_model(TargetConfig(type="openai_compatible", name="j",
                                   base_url="http://mock/v1", model="gpt-4o-mini"))
    with pytest.raises(CostLimitExceeded):
        await gw.complete([{"role": "user", "content": "hi"}], model="j", cache=False)


@pytest.mark.asyncio
async def test_unknown_model_rejected():
    gw = LLMGateway()
    with pytest.raises(KeyError):
        await gw.complete([{"role": "user", "content": "x"}], model="ghost")


@pytest.mark.asyncio
async def test_rate_limiter_paces_calls():
    import time

    from aite.gateway.llm_gateway import InProcessRateLimiter

    limiter = InProcessRateLimiter(rpm=600)  # 0.1s per call
    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.15  # at least two intervals of spacing


def test_price_lookup():
    from aite.utils.cost import estimate_cost_usd, lookup_price

    assert lookup_price("gpt-4o-mini") == (0.15, 0.60)
    assert lookup_price("ollama/llama3") == (0.0, 0.0)
    assert estimate_cost_usd(None, None) is None
    usage = TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert estimate_cost_usd("gpt-4o-mini", usage) == pytest.approx(0.75)
    assert estimate_cost_usd("totally-unknown-model", usage) is None
