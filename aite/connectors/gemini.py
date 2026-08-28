"""Google Gemini connector (generativelanguage REST API, no SDK dependency)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import httpx

from aite.connectors.base import Stopwatch, retry_async, truncate_error
from aite.core.interfaces import TargetSystem
from aite.core.registry import register_connector
from aite.core.types import TargetResponse, TokenUsage
from aite.utils.cost import estimate_cost_usd

if TYPE_CHECKING:
    from aite.core.loader import TargetConfig
    from aite.core.types import InvokeOptions, TestCase


@register_connector("gemini")
class GeminiConnector(TargetSystem):
    name: ClassVar[str] = "gemini"

    def __init__(self, config: TargetConfig) -> None:
        self.config = config
        self.model = config.model or "gemini-2.0-flash"
        self.api_key = config.effective_api_key or ""
        base = config.base_url or "https://generativelanguage.googleapis.com/v1beta"
        self.url = f"{base}/models/{self.model}:generateContent"
        headers = {"Content-Type": "application/json"}
        headers.update(config.headers or {})
        params = {"key": self.api_key} if self.api_key else {}
        self._client = httpx.AsyncClient(timeout=config.timeout_s, headers=headers, params=params)

    async def invoke(self, case: TestCase, options: InvokeOptions) -> TargetResponse:
        data = case.input
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            contents = [
                {"role": "model" if m.get("role") == "assistant" else "user",
                 "parts": [{"text": m.get("content", "")}]}
                for m in data["messages"] if m.get("role") != "system"
            ]
        else:
            text = data if isinstance(data, str) else str(data)
            contents = [{"role": "user", "parts": [{"text": text}]}]
        body: dict = {
            "contents": contents,
            "generationConfig": {
                k: v for k, v in {
                    "temperature": options.temperature if options.temperature is not None
                    else self.config.params.get("temperature"),
                    "maxOutputTokens": options.max_tokens if options.max_tokens is not None
                    else self.config.params.get("max_tokens"),
                    "topP": options.top_p if options.top_p is not None
                    else self.config.params.get("top_p"),
                }.items() if v is not None
            },
        }
        watch = Stopwatch()
        try:
            async def call():
                resp = await self._client.post(self.url, json=body)
                resp.raise_for_status()
                return resp.json()

            payload = await retry_async(call, max_retries=self.config.max_retries)
        except Exception as exc:  # noqa: BLE001
            return TargetResponse(error=truncate_error(exc))
        watch.mark_first_token()
        meta = payload.get("usageMetadata", {})
        usage = TokenUsage(
            prompt_tokens=meta.get("promptTokenCount", 0),
            completion_tokens=meta.get("candidatesTokenCount", 0),
            total_tokens=meta.get("totalTokenCount", 0),
        )
        text = ""
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError):
            pass
        return TargetResponse(
            output=text,
            usage=usage,
            timings=watch.finish(completion_tokens=usage.completion_tokens),
            cost_usd=estimate_cost_usd(self.model, usage),
            raw={"prompt_feedback": payload.get("promptFeedback")},
        )

    async def close(self) -> None:
        await self._client.aclose()
