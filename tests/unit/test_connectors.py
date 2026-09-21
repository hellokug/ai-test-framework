"""Connector tests over httpx.MockTransport (no network)."""

import json

import httpx
import pytest

from aite.connectors.base import Stopwatch, parse_usage
from aite.connectors.custom_http import CustomHttpConnector, extract_path
from aite.connectors.openai_compatible import OpenAICompatibleConnector
from aite.core.loader import TargetConfig
from aite.core.types import InvokeOptions, TestCase


def make_connector(handler, **target_kw):
    config = TargetConfig(base_url="http://mock/v1", model="m", **target_kw)
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleConnector(config, transport=transport), config


class TestOpenAICompatible:
    @pytest.mark.asyncio
    async def test_non_streaming_invoke(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["model"] == "m"
            assert body["messages"] == [{"role": "user", "content": "hi"}]
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})

        conn, _ = make_connector(handler, stream=False)
        resp = await conn.invoke(TestCase(id="c", input="hi"), InvokeOptions())
        await conn.close()
        assert resp.output == "hello" and resp.error is None
        assert resp.usage.total_tokens == 3
        assert resp.timings.total_ms is not None

    @pytest.mark.asyncio
    async def test_streaming_invoke_ttft(self):
        def handler(request: httpx.Request) -> httpx.Response:
            lines = [
                b'data: {"choices":[{"delta":{"content":"Hel"}}]}',
                b'data: {"choices":[{"delta":{"content":"lo"}}]}',
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}',
                b"data: [DONE]",
            ]
            return httpx.Response(200, content=b"\n".join(lines) + b"\n",
                                  headers={"content-type": "text/event-stream"})

        conn, _ = make_connector(handler, stream=True)
        resp = await conn.invoke(TestCase(id="c", input="hi"), InvokeOptions())
        await conn.close()
        assert resp.output == "Hello"
        assert resp.timings.ttft_ms is not None
        assert resp.usage.completion_tokens == 2
        assert resp.timings.tokens_per_sec is not None
        # 逐内容块 mark_token：2 块 → 1 个 ITL 样本（控制事件不入样）
        assert len(resp.timings.itl_samples_ms) == 1

    @pytest.mark.asyncio
    async def test_streaming_usage_cached_tokens(self):
        def handler(request: httpx.Request) -> httpx.Response:
            lines = [
                b'data: {"choices":[{"delta":{"content":"hi"}}]}',
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":1,"total_tokens":101,"prompt_tokens_details":{"cached_tokens":40}}}',
                b"data: [DONE]",
            ]
            return httpx.Response(200, content=b"\n".join(lines) + b"\n",
                                  headers={"content-type": "text/event-stream"})

        conn, _ = make_connector(handler, stream=True)
        resp = await conn.invoke(TestCase(id="c", input="hi"), InvokeOptions())
        await conn.close()
        assert resp.usage.cached_tokens == 40

    @pytest.mark.asyncio
    async def test_retry_on_500_then_success(self):
        state = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}], "usage": {}})

        conn, _ = make_connector(handler, stream=False, max_retries=1)
        resp = await conn.invoke(TestCase(id="c", input="q"), InvokeOptions())
        await conn.close()
        assert state["n"] == 2
        assert resp.output == "ok"

    @pytest.mark.asyncio
    async def test_error_after_retries(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "down"})

        conn, _ = make_connector(handler, stream=False, max_retries=1)
        resp = await conn.invoke(TestCase(id="c", input="q"), InvokeOptions())
        await conn.close()
        assert resp.error and "503" in resp.error

    @pytest.mark.asyncio
    async def test_messages_input_shape(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["messages"] == [
                {"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}]})

        conn, _ = make_connector(handler, stream=False)
        case = TestCase(id="c", input=[{"role": "system", "content": "sys"},
                                       {"role": "user", "content": "q"}])
        resp = await conn.invoke(case, InvokeOptions())
        await conn.close()
        assert resp.output == "ok"

    @pytest.mark.asyncio
    async def test_health_check_reachable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        conn, _ = make_connector(handler)
        assert await conn.health_check() is True
        await conn.close()


class TestCustomHttp:
    @pytest.mark.asyncio
    async def test_template_render_and_extract(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"answer": "42"}})

        config = TargetConfig(
            type="custom_http", base_url="http://mock",
            template={"method": "POST", "path": "/v1/ask",
                      "body": '{"q": {{ case.input | tojson }}}'},
            extract="data.answer")
        conn = CustomHttpConnector(config)
        conn._client._transport = httpx.MockTransport(handler)
        resp = await conn.invoke(TestCase(id="c", input="meaning of life?"),
                                 InvokeOptions())
        await conn.close()
        assert seen["path"] == "/v1/ask"
        assert seen["body"] == {"q": "meaning of life?"}
        assert resp.output == "42"

    def test_extract_path(self):
        assert extract_path({"a": {"b": [1, 2]}}, "a.b.1") == 2
        assert extract_path({"a": 1}, "x") is None
        assert extract_path({"a": 1}, None) == {"a": 1}


@pytest.mark.parametrize("usage,expected", [
    # OpenAI / vLLM / SGLang
    ({"prompt_tokens": 100, "completion_tokens": 5,
      "prompt_tokens_details": {"cached_tokens": 60}}, 60),
    # Anthropic
    ({"input_tokens": 100, "output_tokens": 5,
      "cache_read_input_tokens": 70}, 70),
    # DeepSeek
    ({"prompt_tokens": 100, "completion_tokens": 5,
      "prompt_cache_hit_tokens": 80}, 80),
    # 未回传 → None（区别于 0 = 明确全 miss）
    ({"prompt_tokens": 100, "completion_tokens": 5}, None),
])
def test_parse_usage_cached_tokens(usage, expected):
    parsed = parse_usage({"usage": usage})
    assert parsed.cached_tokens == expected


def test_stopwatch_mark_token_itl():
    watch = Stopwatch()
    watch.mark_token()                     # 首块 = TTFT，不入 ITL 样本
    watch.mark_token()
    watch.mark_token()
    timings = watch.finish(completion_tokens=3)
    assert timings.ttft_ms is not None
    assert len(timings.itl_samples_ms) == 2   # 后续块各累计一个间隔
    assert all(v >= 0 for v in timings.itl_samples_ms)
    empty = Stopwatch().finish()               # 无打点 → 空样本不报错
    assert empty.itl_samples_ms == []
