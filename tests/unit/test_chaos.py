"""可靠性 chaos 框架测试：编排生命周期 / 生效确认回滚 / 兜底恢复 / 注入器。"""


import pytest

from aite.chaos import (
    ChaosVerifyError,
    FaultSpec,
    get_injector,
    list_injectors,
    register_injector,
    run_with_chaos,
    unregister_injector,
    validate_specs,
)


class _FakeInjector:
    """可控假注入器：verify 行为与 recover 异常可编程，动作入 log。"""

    name = "fake"

    def __init__(self, verify_ok=True, recover_fail=False):
        self.verify_ok = verify_ok
        self.recover_fail = recover_fail
        self.log: list[str] = []

    async def inject(self, spec):
        self.log.append(f"inject:{spec.kind}")
        import time

        from aite.chaos.model import FaultHandle

        return FaultHandle(spec=spec, ref={"k": spec.kind}, injected_at=time.time())

    async def verify(self, handle):
        self.log.append(f"verify:{handle.ref['k']}={self.verify_ok}")
        return self.verify_ok

    async def recover(self, handle):
        if self.recover_fail:
            raise RuntimeError("recover boom")
        self.log.append(f"recover:{handle.ref['k']}")


class _FakeWrapper:
    """注册表适配：get_injector() 调用类构造，内部委托共享的 core。"""

    name = "fake"

    def __init__(self, core: _FakeInjector):
        self._core = core

    async def inject(self, spec):
        return await self._core.inject(spec)

    async def verify(self, handle):
        return await self._core.verify(handle)

    async def recover(self, handle):
        await self._core.recover(handle)


@pytest.fixture()
def fake_inj():
    core = _FakeInjector()
    register_injector("fake")(lambda: _FakeWrapper(core))
    yield core
    unregister_injector("fake")


def _spec(kind="f1", injector="fake"):
    return {"injector": injector, "kind": kind, "target": {"x": 1}}


class TestValidateSpecs:
    def test_empty_and_none(self):
        assert validate_specs(None) == []
        assert validate_specs([]) == []

    def test_not_list_rejected(self):
        with pytest.raises(ValueError, match="数组"):
            validate_specs({"injector": "x"})

    def test_missing_kind_rejected(self):
        with pytest.raises(ValueError, match="injector/kind"):
            validate_specs([{"injector": "toxiproxy"}])

    def test_ok(self):
        specs = validate_specs([_spec()])
        assert specs[0].resolve_name() == "fake:f1"


class TestRunWithChaos:
    async def test_happy_path_event_order(self, fake_inj):
        order = []

        async def business():
            order.append("business")
            fake_inj.log.append("business-marker")
            return 42

        result, events = await run_with_chaos([_spec("f1")], business)
        assert result == 42
        assert order == ["business"]
        phases = [e["phase"] for e in events]
        assert phases == ["injected", "verified", "recovered"]
        # 兜底恢复在业务之后
        assert fake_inj.log == ["inject:f1", "verify:f1=True",
                                "business-marker", "recover:f1"]

    async def test_multiple_faults_recovered_reverse_order(self, fake_inj):
        async def business():
            return None

        _, events = await run_with_chaos(
            [_spec("a"), _spec("b")], business)
        recovers = [e["fault"] for e in events if e["phase"] == "recovered"]
        assert recovers == ["fake:b", "fake:a"]    # 逆序恢复

    async def test_verify_failure_rolls_back(self, fake_inj):
        fake_inj.verify_ok = False
        executed = []

        async def business():
            executed.append(1)

        with pytest.raises(ChaosVerifyError, match="未确认生效"):
            await run_with_chaos([_spec("f1")], business)
        assert executed == []                       # 业务未执行
        assert "recover:f1" in fake_inj.log         # 已注入部分被兜底回收

    async def test_business_exception_still_recovers(self, fake_inj):
        async def business():
            raise RuntimeError("biz boom")

        with pytest.raises(RuntimeError, match="biz boom"):
            await run_with_chaos([_spec("f1")], business)
        assert "recover:f1" in fake_inj.log         # 异常路径恢复不缺席

    async def test_recover_failure_logged_not_raised(self, fake_inj):
        fake_inj.recover_fail = True

        async def business():
            return "ok"

        result, events = await run_with_chaos([_spec("f1")], business)
        assert result == "ok"                       # 恢复失败不吞业务结果
        phases = [e["phase"] for e in events]
        assert "recover_failed" in phases


class TestInjectorRegistry:
    def test_list_contains_builtins(self):
        import aite.chaos.injectors  # noqa: F401  (注册副作用)

        assert {"toxiproxy", "process"} <= set(list_injectors())

    def test_unknown_injector_lists_available(self):
        import aite.chaos.injectors  # noqa: F401

        with pytest.raises(KeyError, match="available"):
            get_injector("nope")


class TestToxiproxyInjector:
    @pytest.fixture()
    def inj(self):
        from aite.chaos.injectors.toxiproxy import ToxiproxyInjector

        return ToxiproxyInjector()

    def _spec(self, kind="latency", params=None):
        return FaultSpec(injector="toxiproxy", kind=kind,
                         target={"downstream": "http://localhost:11434/v1",
                                 "listen_port": 18434},
                         params=params or {})

    async def test_inject_creates_proxy_and_toxic(self, inj, monkeypatch):
        calls = []

        class FakeResp:
            def __init__(self, code=200, text=""):
                self.status_code = code
                self.text = text

            def json(self):
                return {"enabled": True, "toxics": ["t"]}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None):
                calls.append(("POST", url, json))
                return FakeResp(200)

            async def get(self, url):
                calls.append(("GET", url))
                return FakeResp(200)

            async def delete(self, url):
                calls.append(("DELETE", url))
                return FakeResp(204)

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        handle = await inj.inject(self._spec("latency", {"latency_ms": 2000}))
        posts = [c for c in calls if c[0] == "POST"]
        assert posts[0][1].endswith("/proxies")
        assert posts[0][2]["upstream"] == "localhost:11434"   # URL 归一
        assert posts[0][2]["listen"].endswith(":18434")
        assert posts[1][2]["type"] == "latency"
        assert posts[1][2]["attributes"] == {"latency": 2000, "jitter": 0}
        assert await inj.verify(handle) is True
        await inj.recover(handle)
        assert any(c[0] == "DELETE" for c in calls)

    def test_unknown_kind_rejected(self, inj):
        import asyncio

        with pytest.raises(ValueError, match="不支持故障类型"):
            asyncio.run(inj.inject(self._spec("earthquake")))
