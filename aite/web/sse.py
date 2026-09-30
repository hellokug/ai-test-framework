"""SSE 事件流共享助手（runs 与四域任务路由共用，原则 3/6）。

频道语义：EventBus.close 会上报 `end` 帧，订阅者收到后必须终止（否则永久挂起，
见 aite/events.py）；终态帧（done/error/cancelled/succeeded/failed）先于 end 帧，
此处双保险同时截断。
"""

from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import StreamingResponse

_TERMINAL_TYPES = frozenset({"done", "error", "end", "cancelled",
                             "succeeded", "failed"})


def stream_events(bus, request: Request, channel: str) -> StreamingResponse:
    """订阅 bus 频道并转发为 text/event-stream；终态或 end 帧后关流。"""

    async def gen():
        async for event in bus.subscribe(channel):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") in _TERMINAL_TYPES:
                break

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
