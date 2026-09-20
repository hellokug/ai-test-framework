"""进程故障注入器（kill / suspend），psutil 懒加载。

⚠️ 高危能力：按 pid 精确定位（推荐）或按进程名（取首个匹配，文档级警示：
勿用宽泛名称误杀 master 自身/系统进程）。
- ``kill``：terminate 目标进程；recover 无操作（幂等：进程已不在）
- ``suspend``：挂起目标进程；recover = resume
"""

from __future__ import annotations

import os

from aite.chaos.injectors.base import Injector, register_injector
from aite.chaos.model import FaultHandle, FaultSpec
from aite.utils.logging import get_logger

logger = get_logger(__name__)


def _psutil():
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("进程故障注入需要 psutil：pip install psutil") from exc
    return psutil


@register_injector("process")
class ProcessInjector(Injector):
    name = "process"
    kinds = ["kill", "suspend"]

    async def inject(self, spec: FaultSpec) -> FaultHandle:
        if spec.kind not in ("kill", "suspend"):
            raise ValueError(f"process 不支持故障类型 {spec.kind!r}"
                             "（可用: kill / suspend）")
        psutil = _psutil()
        proc = self._resolve_process(psutil, spec.target)
        pid = proc.pid
        if spec.kind == "kill":
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except psutil.TimeoutExpired:
                proc.kill()
            logger.info("chaos[process] kill pid=%s", pid)
        else:
            proc.suspend()               # 挂起/恢复是 Process 方法（psutil 无模块级 suspend）
            logger.info("chaos[process] suspend pid=%s", pid)
        return FaultHandle(spec=spec, ref={"pid": pid, "kind": spec.kind},
                           injected_at=self.now())

    async def verify(self, handle: FaultHandle) -> bool:
        psutil = _psutil()
        pid, kind = handle.ref["pid"], handle.ref["kind"]
        if not psutil.pid_exists(pid):
            return kind == "kill"          # kill 的生效 = 进程不存在
        if kind != "suspend":
            return False
        try:
            return psutil.Process(pid).status() == psutil.STATUS_STOPPED
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    async def recover(self, handle: FaultHandle) -> None:
        if handle.ref["kind"] != "suspend":
            return                          # kill 无恢复语义（幂等）
        psutil = _psutil()
        pid = handle.ref["pid"]
        if psutil.pid_exists(pid):
            psutil.Process(pid).resume()
            logger.info("chaos[process] resume pid=%s", pid)

    @staticmethod
    def _resolve_process(psutil, target: dict):
        if target.get("pid"):
            try:
                return psutil.Process(int(target["pid"]))
            except psutil.NoSuchProcess as exc:
                raise ValueError(f"目标进程不存在: pid={target['pid']}") from exc
        name = str(target.get("name") or "").strip().lower()
        if not name:
            raise ValueError("process 故障 target 需要 pid 或 name（模糊子串）")
        matches = [proc for proc in psutil.process_iter(["pid", "name"])
                   if name in (proc.info.get("name") or "").lower()
                   and proc.info.get("pid") != os.getpid()]
        if not matches:
            raise ValueError(f"按名称模糊匹配未找到进程: {name!r}")
        if len(matches) > 1:
            logger.warning("chaos[process] 名称 %r 命中 %d 个进程，取 pid 最小 %s",
                           name, len(matches),
                           sorted(p.info["pid"] for p in matches)[:5])
        return min(matches, key=lambda p: p.info["pid"])
