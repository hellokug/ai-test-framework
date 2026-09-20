"""process 注入器实弹单测：对真实子进程做 suspend → verify → recover 全链。

此前单测全 mock、真实链路首次执行才暴露 psutil.suspend 模块级函数不存在
（实为 Process 方法）——本文件保证注入器对真进程的主路径持续被真实执行。
"""

import asyncio
import subprocess
import sys

import pytest

from aite.chaos.injectors.process import ProcessInjector
from aite.chaos.model import FaultSpec

psutil = pytest.importorskip("psutil")


def _spawn_sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _spec(pid: int, kind: str) -> FaultSpec:
    return FaultSpec(injector="process", kind=kind, target={"pid": pid})


async def test_suspend_verify_recover_roundtrip():
    proc = _spawn_sleeper()
    try:
        injector = ProcessInjector()
        handle = await injector.inject(_spec(proc.pid, "suspend"))
        assert handle.ref == {"pid": proc.pid, "kind": "suspend"}
        assert await injector.verify(handle) is True
        assert psutil.Process(proc.pid).status() == psutil.STATUS_STOPPED

        await injector.recover(handle)
        assert await injector.verify(handle) is False      # 恢复后不再满足挂起态
        assert psutil.Process(proc.pid).status() != psutil.STATUS_STOPPED
    finally:
        proc.terminate()


async def test_kill_verify_and_idempotent_recover():
    proc = _spawn_sleeper()
    try:
        injector = ProcessInjector()
        handle = await injector.inject(_spec(proc.pid, "kill"))
        assert await injector.verify(handle) is True       # kill 生效 = 进程不在
        await injector.recover(handle)                     # kill 恢复幂等无操作
        assert not psutil.pid_exists(proc.pid)
    finally:
        if psutil.pid_exists(proc.pid):
            psutil.Process(proc.pid).terminate()


async def test_missing_target_raises_explicit():
    injector = ProcessInjector()
    with pytest.raises(ValueError, match="目标进程不存在"):
        await injector.inject(_spec(999_999, "suspend"))
    with pytest.raises(ValueError, match="pid 或 name"):
        await injector.inject(FaultSpec(injector="process", kind="kill",
                                        target={}))


def _async_run(coro):
    return asyncio.run(coro)
