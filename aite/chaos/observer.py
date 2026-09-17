"""观测层（模块 I5 观测一期）：推理探针流量 → pass/fail 时间线 → RTO。

作为背景流量持续观测被测端点：流式推理请求按固定间隔（缺省 5s）打点，
成功标 pass、失败标 fail，形成按时间打点的视图；RTO 由 fail→恢复后首个
pass 的时间差判定（精度受探针间隔限制 ±interval）。与故障注入编排
（controller）组合：注入全部生效后开始观测，观测结束后恢复并补测
恢复后窗口（post_window），使 RTO 可测。

探针即轻量评测：HTTP 200 且流式内容非空 = pass；连接失败/非 200/空内容
均标 fail 并记录错误。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, replace

import httpx

from aite.chaos.controller import run_with_chaos
from aite.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_PROMPT = "ping"


@dataclass
class ProbeConfig:
    """观测探针配置（REST observe 段 / 编排入参）。"""

    url: str
    model: str
    api_key: str = "EMPTY"
    interval: float = 5.0            # 打点间隔（秒）
    duration: float = 120.0          # 观测时长（秒，故障窗口内的观测期）
    timeout: float = 30.0
    prompt: str = _DEFAULT_PROMPT
    headers: dict = field(default_factory=dict)


async def probe_once(cfg: ProbeConfig, client: httpx.AsyncClient) -> dict:
    """单次流式探针：HTTP 200 且产出非空内容 = pass，否则 fail。"""
    rec: dict = {"t": time.time(), "ok": 0, "ttft_ms": None,
                 "latency_ms": None, "content_len": 0, "error": None}
    t0 = time.perf_counter()
    try:
        async with client.stream(
                "POST", cfg.url,
                json={"model": cfg.model, "stream": True,
                      "stream_options": {"include_usage": True},
                      "messages": [{"role": "user", "content": cfg.prompt}]},
                headers={"Authorization": f"Bearer {cfg.api_key}", **cfg.headers},
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread())[:200].decode("utf-8", "replace")
                rec["error"] = f"HTTP {resp.status_code}: {body}"
                rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                return rec
            ttft: float | None = None
            content: list[str] = []
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                if ttft is None:
                    ttft = (time.perf_counter() - t0) * 1000
                data = line[6:].strip()
                if data == "[DONE]":
                    continue          # 读完至流自然结束（keep-alive，同 locust 教训）
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                if delta.get("content"):
                    content.append(delta["content"])
            rec["ttft_ms"] = round(ttft, 1) if ttft is not None else None
            rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            rec["content_len"] = sum(len(c) for c in content)
            rec["ok"] = 1 if content else 0
            if not content:
                rec["error"] = "HTTP 200 但流式内容为空"
    except (httpx.HTTPError, OSError) as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return rec


async def run_observation(cfg: ProbeConfig,
                          client: httpx.AsyncClient | None = None) -> list[dict]:
    """按 interval 持续打点直至 duration（首个探针立即发出）。"""
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=cfg.timeout)
    probes: list[dict] = []
    deadline = time.monotonic() + cfg.duration
    try:
        while True:
            probes.append(await probe_once(cfg, client))
            if time.monotonic() + cfg.interval >= deadline:
                break
            await asyncio.sleep(cfg.interval)
    finally:
        if own:
            await client.aclose()
    return probes


def compute_rto(probes: list[dict], events: list[dict]) -> dict:
    """RTO 判定：last_fail → 恢复后首个 pass 的时间差（精度 ±探针间隔）。

    无 fail = 服务未受影响（affected=False）；窗口内全部 fail 且恢复后
    窗口无 pass = rto 不可判定（None，需延长 post 窗口重测）。
    """
    fails = [p for p in probes if not p["ok"]]
    if not fails:
        return {"affected": False, "fail_count": 0, "first_fail": None,
                "last_fail": None, "recovered_at": None, "rto_seconds": None}
    first_fail, last_fail = fails[0]["t"], fails[-1]["t"]
    recovered = next((p["t"] for p in probes if p["ok"] and p["t"] > last_fail),
                     None)
    return {"affected": True, "fail_count": len(fails),
            "first_fail": first_fail, "last_fail": last_fail,
            "recovered_at": recovered,
            "rto_seconds": round(recovered - last_fail, 3) if recovered else None}


def _summary(probes: list[dict]) -> dict:
    n = len(probes)
    passed = sum(p["ok"] for p in probes)
    return {"total": n, "pass": passed, "fail": n - passed,
            "pass_rate": round(passed / n, 4) if n else None}


async def observe_under_chaos(specs_raw, cfg: ProbeConfig,
                              post_window: float | None = None) -> dict:
    """观测编排：注入全部故障 → 窗口内持续观测 → 恢复 → 恢复后补测窗口。

    post_window（缺省 = interval × 3）用于捕捉恢复后的首个 pass，使 RTO
    可判定；补测探针同样进入时间线（时间戳天然有序）。
    """
    obs, events = await run_with_chaos(specs_raw, lambda: run_observation(cfg))
    post_cfg = replace(cfg, duration=post_window if post_window is not None
                       else cfg.interval * 3)
    post = await run_observation(post_cfg)
    probes = obs + post
    return {"events": events, "probes": probes, "rto": compute_rto(probes, events),
            "summary": _summary(probes)}
