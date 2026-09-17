"""观测层测试：流式探针 / pass-fail 时间线 / RTO 判定 / 注入+观测编排。"""

import asyncio
import time

import httpx
import pytest

from aite.chaos.injectors.base import Injector, register_injector, unregister_injector
from aite.chaos.model import FaultHandle, FaultSpec
from aite.chaos.observer import ProbeConfig, compute_rto, observe_under_chaos, probe_once, run_observation

SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
    "data: [DONE]\n\n"
)


def _mock_transport(fail_first: int = 0, counter: dict | None = None):
    """前 fail_first 次返回 500，其后返回 SSE 流。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if counter is not None:
            counter["requests"] = state["n"]
        if state["n"] <= fail_first:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=SSE_BODY, headers={
            "content-type": "text/event-stream"})

    return httpx.MockTransport(handler)


def _cfg(duration: float = 0.1, interval: float = 0.03, **kw) -> ProbeConfig:
    return ProbeConfig(url="http://probe.test/v1/chat/completions",
                       model="m", api_key="EMPTY", interval=interval,
                       duration=duration, timeout=5.0, **kw)


@pytest.mark.unit
class TestProbe:
    def test_probe_pass_streaming(self):
        client = httpx.AsyncClient(transport=_mock_transport())

        async def run():
            return await probe_once(_cfg(), client)

        rec = asyncio.run(run())
        assert rec["ok"] == 1 and rec["content_len"] == 5
        assert rec["ttft_ms"] is not None and rec["error"] is None

    def test_probe_fail_http_500(self):
        client = httpx.AsyncClient(transport=_mock_transport(fail_first=99))

        async def run():
            return await probe_once(_cfg(), client)

        rec = asyncio.run(run())
        assert rec["ok"] == 0 and "HTTP 500" in rec["error"]

    def test_run_observation_timeline(self):
        client = httpx.AsyncClient(transport=_mock_transport(fail_first=2))

        async def run():
            return await run_observation(_cfg(duration=0.12, interval=0.04),
                                         client)

        probes = asyncio.run(run())
        assert len(probes) >= 2
        assert [p["ok"] for p in probes[:2]] == [0, 0]      # 故障窗口 fail
        assert probes[-1]["ok"] == 1                        # 恢复后 pass


class TestRto:
    def test_rto_from_fail_timeline(self):
        base = time.time()
        probes = [{"t": base, "ok": 1}, {"t": base + 5, "ok": 0},
                  {"t": base + 10, "ok": 0}, {"t": base + 15, "ok": 1}]
        rto = compute_rto(probes, [])
        assert rto["affected"] is True and rto["fail_count"] == 2
        assert rto["rto_seconds"] == pytest.approx(5.0)

    def test_rto_not_affected(self):
        base = time.time()
        rto = compute_rto([{"t": base, "ok": 1}], [])
        assert rto["affected"] is False and rto["rto_seconds"] is None

    def test_rto_undetectable_without_post_pass(self):
        base = time.time()
        rto = compute_rto([{"t": base, "ok": 0}], [])
        assert rto["affected"] is True and rto["rto_seconds"] is None


class _MockInjector(Injector):
    """测试武器：注入即生效、恢复即解除（无副作用）。"""

    name = "mock"
    kinds = ["delay"]

    async def inject(self, spec: FaultSpec) -> FaultHandle:
        return FaultHandle(spec=spec, ref={"on": True}, injected_at=self.now())

    async def verify(self, handle: FaultHandle) -> bool:
        return bool(handle.ref.get("on"))

    async def recover(self, handle: FaultHandle) -> None:
        handle.ref["on"] = False


class TestObserveUnderChaos:
    def test_orchestration_inject_observe_recover(self):
        register_injector("mock")(_MockInjector)      # 注册表为装饰器形态
        try:
            spec = {"injector": "mock", "kind": "delay",
                    "target": {"who": "test"},
                    "params": {"ms": 100}, "name": "mock-delay"}
            result = asyncio.run(observe_under_chaos(
                [spec], _cfg(duration=0.1, interval=0.04), post_window=0.05))
            phases = [e["phase"] for e in result["events"]]
            assert "injected" in phases and "verified" in phases
            assert "recovered" in phases
            assert result["probes"] and result["summary"]["total"] >= 1
            assert result["rto"]["affected"] in (True, False)   # 形状完整
        finally:
            unregister_injector("mock")
