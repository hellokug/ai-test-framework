"""Anthropic Claude connector (Messages API over REST, no SDK dependency)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import httpx

from aite.connectors.base import (
    Stopwatch,
    case_to_messages,
    retry_async,
    truncate_error,
)
from aite.core.interfaces import TargetSystem
from aite.core.registry import register_connector
from aite.core.types import TargetResponse, TokenUsage
from aite.utils.cost import estimate_cost_usd

if TYPE_CHECKING:
    from aite.core.loader import TargetConfig
    from aite.core.types import InvokeOptions, TestCase


@register_connector("anthropic")
class AnthropicConnector(TargetSystem):
    name: ClassVar[str] = "anthropic"

    def __init__(self, config: TargetConfig) -> None:
        self.config = config
        self.model = config.model
        base = config.base_url or "https://api.anthropic.com/v1"
        self.url = f"{base}/messages"
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        key = config.effective_api_key
        if key:
            headers["x-api-key"] = key
        headers.update(config.headers or {})
        self._client = httpx.AsyncClient(timeout=config.timeout_s, headers=headers)

    async def invoke(self, case: TestCase, options: InvokeOptions) -> TargetResponse:
        messages = case_to_messages(case, options)
        # Anthropic requires alternating user/assistant; merge consecutive same-role
        merged: list[dict[str, str]] = []
        for m in messages:
            if merged and merged[-1]["role"] == m["role"]:
                merged[-1]["content"] += "\n\n" + m["content"]
            else:
                merged.append(dict(m))
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        body: dict = {"model": self.model, "messages": [m for m in merged if m["role"] != "system"]}
        if system:
            body["system"] = system
        body["max_tokens"] = int(
            options.max_tokens if options.max_tokens is not None
             else self.config.params.get("max_tokens", 1024)
        )
        if options.temperature is not None:
            body["temperature"] = options.temperature
        elif self.config.params.get("temperature") is not None:
            body["temperature"] = self.config.params["temperature"]
        body.update({k: v for k, v in self.config.params.items()
                     if k not in ("temperature", "max_tokens")})

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
        usage = TokenUsage(
            prompt_tokens=payload.get("usage", {}).get("input_tokens", 0),
            completion_tokens=payload.get("usage", {}).get("output_tokens", 0),
            total_tokens=(payload.get("usage", {}).get("input_tokens", 0)
                          + payload.get("usage", {}).get("output_tokens", 0)),
        )
        text = "".join(block.get("text", "") for block in payload.get("content", [])
                       if block.get("type") == "text")
        return TargetResponse(
            output=text,
            usage=usage,
            timings=watch.finish(completion_tokens=usage.completion_tokens),
            cost_usd=estimate_cost_usd(self.model, usage),
            raw={"stop_reason": payload.get("stop_reason")},
        )

    async def close(self) -> None:
        await self._client.aclose()
