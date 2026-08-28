"""Async helpers: bounded gather, graceful timeout, jittered sleep."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def bounded_gather(coros, limit: int, *, return_exceptions: bool = False):
    """Run awaitables with a concurrency semaphore, preserving order."""
    if limit <= 0:
        limit = 1
    sem = asyncio.Semaphore(limit)

    async def _wrap(c):
        async with sem:
            return await c

    return await asyncio.gather(*(_wrap(c) for c in coros), return_exceptions=return_exceptions)


async def with_timeout(awaitable: Awaitable[T], timeout_s: float | None) -> T:
    if timeout_s is None or timeout_s <= 0:
        return await awaitable
    return await asyncio.wait_for(awaitable, timeout=timeout_s)


async def sleep_jitter(base_s: float, jitter: float = 0.3) -> None:
    await asyncio.sleep(base_s * (1 + random.uniform(0, jitter)))
