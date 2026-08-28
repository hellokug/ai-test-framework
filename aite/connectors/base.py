"""Connector shared helpers: request retry, message normalization, timings."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import httpx

from aite.core.types import Timings, TokenUsage

if TYPE_CHECKING:
    from aite.core.types import InvokeOptions, TestCase

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, (httpx.TransportError, ConnectionError))


async def retry_async(fn, *, max_retries: int = 2, base_delay: float = 1.0):
    """Retry with exponential backoff on retryable HTTP errors. Raises last error."""
    attempt, delay = 0, base_delay
    while True:
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_retries or not is_retryable(exc):
                raise
            await asyncio.sleep(delay)
            attempt += 1
            delay *= 2


class Stopwatch:
    """Elapsed-time helper producing Timings (ttft recorded on first mark)."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._ttft: float | None = None

    def mark_first_token(self) -> None:
        if self._ttft is None:
            self._ttft = (time.perf_counter() - self._start) * 1000

    def finish(self, *, completion_tokens: int | None = None) -> Timings:
        total_ms = (time.perf_counter() - self._start) * 1000
        tps = None
        if completion_tokens and total_ms > 0 and self._ttft is not None:
            gen_ms = max(total_ms - self._ttft, 1e-6)
            tps = completion_tokens / (gen_ms / 1000)
        elif completion_tokens and total_ms > 0:
            tps = completion_tokens / (total_ms / 1000)
        return Timings(ttft_ms=round(self._ttft, 3) if self._ttft is not None else None,
                       total_ms=round(total_ms, 3), tokens_per_sec=round(tps, 3) if tps else None)


def case_to_messages(case: TestCase, options: InvokeOptions | None = None) -> list[dict[str, str]]:
    """Interpret ``case.input`` as chat messages.

    Supported: str -> single user message; list[dict] -> messages verbatim;
    dict{messages|prompt|question|input} -> unwrap.
    """
    data = case.input
    if isinstance(data, str):
        return [{"role": "user", "content": data}]
    if isinstance(data, list) and all(isinstance(m, dict) and "role" in m for m in data):
        return [{"role": m["role"], "content": m.get("content", "")} for m in data]
    if isinstance(data, dict):
        if isinstance(data.get("messages"), list):
            return data["messages"]
        for key in ("prompt", "question", "input", "instruction"):
            if isinstance(data.get(key), str):
                return [{"role": "user", "content": data[key]}]
    return [{"role": "user", "content": str(data)}]


def parse_usage(payload: dict[str, Any] | None) -> TokenUsage | None:
    if not payload:
        return None
    usage = payload.get("usage") or {}
    if not usage:
        return None
    pt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    ct = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    tt = usage.get("total_tokens") or (pt + ct)
    return TokenUsage(prompt_tokens=int(pt), completion_tokens=int(ct), total_tokens=int(tt))


def truncate_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:2000]
