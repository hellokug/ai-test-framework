"""Toxiproxy 网络故障注入器（用户态 TCP 代理，跨平台无 root）。

工作方式：在 toxiproxy server 上创建代理（listen_port → downstream），
业务流量改走代理端口；故障以 toxic 注入代理。aite 的被测 suite 把
``target.base_url`` 指向代理监听端口即可（显式配置，一期不做自动改写）。

依赖：外部 toxiproxy server（Go 单二进制；缺省连 http://127.0.0.1:8474，
可用 target.api 覆盖）。

kind → toxic 映射：
- ``latency``  params: {latency_ms, jitter_ms?}     数据延迟
- ``timeout``  params: {timeout_ms?}                连接挂起（数据不再传输）
- ``bandwidth`` params: {rate_kb}                   限带宽（KB/s）
"""

from __future__ import annotations

import httpx

from aite.chaos.injectors.base import Injector, register_injector
from aite.chaos.model import FaultHandle, FaultSpec
from aite.utils.logging import get_logger

logger = get_logger(__name__)

_TOXIC_TYPES = {
    "latency": lambda p: {"latency": int(p.get("latency_ms", 1000)),
                          "jitter": int(p.get("jitter_ms", 0))},
    "timeout": lambda p: {"timeout": int(p.get("timeout_ms", 0))},
    "bandwidth": lambda p: {"rate": int(p.get("rate_kb", 64)) * 1024},
}


@register_injector("toxiproxy")
class ToxiproxyInjector(Injector):
    kinds = ["latency", "timeout", "bandwidth"]
    name = "toxiproxy"

    async def inject(self, spec: FaultSpec) -> FaultHandle:
        api, proxy_name, listen, upstream = self._resolve(spec)
        async with httpx.AsyncClient(timeout=10) as client:
            # 代理名复用：同 fault 名重复注入先清理旧代理（幂等起步）
            await self._delete_proxy(client, api, proxy_name)
            resp = await client.post(f"{api}/proxies", json={
                "name": proxy_name, "listen": f"0.0.0.0:{listen}",
                "upstream": upstream, "enabled": True})
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"toxiproxy 创建代理失败: {resp.text[:200]}")
            toxic = _TOXIC_TYPES[spec.kind](spec.params)
            resp = await client.post(
                f"{api}/proxies/{proxy_name}/toxics",
                json={"name": f"{proxy_name}-toxic", "type": spec.kind,
                      "stream": "downstream", "toxicity": 1.0,
                      "attributes": toxic})
            if resp.status_code not in (200, 201):
                await self._delete_proxy(client, api, proxy_name)
                raise RuntimeError(f"toxiproxy 注入 toxic 失败: {resp.text[:200]}")
        logger.info("chaos[%s] 注入 %s → %s（代理 :%s）",
                    proxy_name, spec.kind, upstream, listen)
        return FaultHandle(spec=spec, ref={"api": api, "proxy": proxy_name},
                           injected_at=self.now())

    async def verify(self, handle: FaultHandle) -> bool:
        api, proxy_name = handle.ref["api"], handle.ref["proxy"]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{api}/proxies/{proxy_name}")
                if resp.status_code != 200:
                    return False
                data = resp.json()
                return bool(data.get("enabled")) and bool(data.get("toxics"))
        except httpx.HTTPError:
            return False   # 控制面不可达 = 无法确认生效（与无效应同等对待）

    async def recover(self, handle: FaultHandle) -> None:
        api, proxy_name = handle.ref["api"], handle.ref["proxy"]
        async with httpx.AsyncClient(timeout=10) as client:
            await self._delete_proxy(client, api, proxy_name)
        logger.info("chaos[%s] 已恢复（代理移除）", proxy_name)

    # ---------------------------------------------------------------- internal

    @staticmethod
    def _resolve(spec: FaultSpec) -> tuple[str, str, int, str]:
        target = spec.target
        api = str(target.get("api") or "http://127.0.0.1:8474").rstrip("/")
        listen = int(target["listen_port"])
        downstream = str(target["downstream"])
        # downstream 归一为 host:port（容许传入完整 URL）
        if "://" in downstream:
            rest = downstream.split("://", 1)[1]
            downstream = rest.split("/", 1)[0]
        if spec.kind not in _TOXIC_TYPES:
            raise ValueError(f"toxiproxy 不支持故障类型 {spec.kind!r}"
                             f"（可用: {sorted(_TOXIC_TYPES)}）")
        proxy_name = f"aite-{spec.kind}-{abs(hash(spec.resolve_name())) % 10_000}"
        return api, proxy_name, listen, downstream

    @staticmethod
    async def _delete_proxy(client: httpx.AsyncClient, api: str, name: str) -> None:
        resp = await client.delete(f"{api}/proxies/{name}")
        if resp.status_code not in (200, 204, 404):
            logger.warning("toxiproxy 清理代理 %s 返回 %s", name, resp.status_code)
