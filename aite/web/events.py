"""Process-local event bus for SSE progress streaming.

Zero-middleware default; the EventBus protocol maps 1:1 onto a Redis pub/sub
backend for the future `aite[distributed]` extra without touching call sites.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator


class EventBus:
    def __init__(self, history: int = 500) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._history_max = history

    def publish(self, channel: str, event: dict) -> None:
        for q in list(self._subs.get(channel, [])):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)
        hist = self._history[channel]
        hist.append(event)
        if len(hist) > self._history_max:
            del hist[: len(hist) - self._history_max]

    async def subscribe(self, channel: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[channel].append(q)
        try:
            for event in list(self._history.get(channel, [])):
                yield event
            while True:
                event = await q.get()
                yield event
                if event.get("type") == "end":
                    break
        finally:
            self._subs[channel].remove(q)

    def close(self, channel: str) -> None:
        self.publish(channel, {"type": "end"})
