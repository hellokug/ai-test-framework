"""OpenAI-compatible connector (chat/completions over REST).

Works with OpenAI, DeepSeek, Qwen/DashScope, vLLM, Ollama (/v1), Ollama native
fallback, and any OpenAI-schema server. Streaming is used to capture TTFT when
``stream: true``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

import httpx

from aite.connectors.base import (
    Stopwatch,
    case_to_messages,
    parse_usage,
    retry_async,
    truncate_error,
)
from aite.core.interfaces import TargetSystem
from aite.core.registry import register_connector
from aite.core.types import TargetResponse
from aite.utils.cost import estimate_cost_usd

if TYPE_CHECKING:
    from aite.core.loader import TargetConfig
    from aite.core.types import InvokeOptions, TestCase


@register_connector("openai_compatible")
class OpenAICompatibleConnector(TargetSystem):
    name: ClassVar[str] = "openai_compatible"

    def __init__(self, config: TargetConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self.model = config.model
        base = config.base_url or "https://api.openai.com/v1"
        self.chat_url = f"{base}/chat/completions"
        self.api_key = config.effective_api_key
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(config.headers or {})
        self._client = httpx.AsyncClient(
            timeout=config.timeout_s, headers=headers, transport=transport
        )

    def _build_body(self, case: TestCase, options: InvokeOptions) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": case_to_messages(case, options),
        }
        merged: dict[str, Any] = dict(self.config.params or {})
        if options.max_tokens is not None:
            merged["max_tokens"] = options.max_tokens
        if options.temperature is not None:
            merged["temperature"] = options.temperature
        if options.top_p is not None:
            merged["top_p"] = options.top_p
        if options.stop:
            merged["stop"] = options.stop
        if options.seed is not None:
            merged["seed"] = options.seed
        merged.update(options.extra or {})
        body.update(merged)
        return body

    async def invoke(self, case: TestCase, options: InvokeOptions) -> TargetResponse:
        body = self._build_body(case, options)
        want_stream = options.stream if options.stream is not None else self.config.stream
        body["stream"] = bool(want_stream)
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        watch = Stopwatch()
        try:
            if want_stream:
                return await self._invoke_stream(body, watch)
            return await self._invoke_json(body, watch)
        except Exception as exc:  # noqa: BLE001
            return TargetResponse(error=truncate_error(exc))

    async def _invoke_json(self, body: dict[str, Any], watch: Stopwatch) -> TargetResponse:
        async def call():
            resp = await self._client.post(self.chat_url, json=body)
            resp.raise_for_status()
            return resp.json()

        payload = await retry_async(call, max_retries=self.config.max_retries)
        watch.mark_first_token()  # non-streaming: no TTFT distinction
        usage = parse_usage(payload)
        text = ""
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            text = json.dumps(payload.get("choices", []), ensure_ascii=False)
        return TargetResponse(
            output=text,
            usage=usage,
            timings=watch.finish(completion_tokens=usage.completion_tokens if usage else None),
            cost_usd=estimate_cost_usd(self.model, usage),
            raw={"finish_reason": _finish_reason(payload)},
        )

    async def _invoke_stream(self, body: dict[str, Any], watch: Stopwatch) -> TargetResponse:
        async def open_stream():
            req = self._client.build_request("POST", self.chat_url, json=body)
            resp = await self._client.send(req, stream=True)
            resp.raise_for_status()
            return resp

        resp = await retry_async(open_stream, max_retries=self.config.max_retries)
        chunks: list[str] = []
        usage = None
        finish_reason = None
        try:
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if event.get("usage"):
                    usage = parse_usage(event)
                if not event.get("choices"):
                    continue
                delta = event["choices"][0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    watch.mark_token()   # 首块记 TTFT，后续块累计 ITL 样本
                    chunks.append(piece)
                if event["choices"][0].get("finish_reason"):
                    finish_reason = event["choices"][0]["finish_reason"]
        finally:
            await resp.aclose()
        timings = watch.finish(completion_tokens=usage.completion_tokens if usage else None)
        return TargetResponse(
            output="".join(chunks),
            usage=usage,
            timings=timings,
            cost_usd=estimate_cost_usd(self.model, usage),
            raw={"finish_reason": finish_reason, "chunks": len(chunks)},
        )

    async def health_check(self) -> bool:
        try:
            base = (self.config.base_url or "https://api.openai.com/v1")
            resp = await self._client.get(f"{base}/models")
            return resp.status_code in (200, 401, 403, 404)  # reachable is enough
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()


def _finish_reason(payload: dict[str, Any]) -> str | None:
    try:
        return payload["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError):
        return None
