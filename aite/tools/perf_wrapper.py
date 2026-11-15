"""evalscope perf 协作停止 wrapper（立即停止的执行端，由 perf_adapter 拉起）。

背景：aite 以子进程方式跑 ``evalscope perf``（不自研压测客户端），而上游在
Windows 上不装任何信号处理器（``perf/main.py`` 仅 POSIX add_signal_handlers），
POSIX 的信号路径也是「取消全部在途任务」——两条路都给不出「停止派发新请求、
等待在途返回」的语义。唯一可借力的机制是策略层的 **duration 软退出**：
closed/open loop 派发循环每轮检查 ``time.perf_counter() >= deadline``，命中即
break 并 ``await asyncio.gather(*in_flight)`` 排空在途——这正是「立即停止」。

本 wrapper 因此只做一件事：给运行中的压测装一个**可触发的 deadline**。

- ``StopClock``：替身 ``time`` 模块，只代理 ``perf_counter``；停止请求到达时
  给时钟整体前移 STOP_CLOCK_JUMP_S。deadline 是启动时用真实时钟算的
  （``BenchmarkStrategy._compute_deadline`` 不换），跳变后所有「now >= deadline」
  判断立即命中 → 派发循环 break → 排空在途 → 该档 summary 正常落盘。
- 入口守卫：ClosedLoop/OpenLoop ``run`` 与 ``run_one_benchmark``（多档 sweep
  的档间 + probe 摸高的轮间）在停止已请求时抛 ``GracefulStopRequested``，
  不再发起新档位；当前档位不受影响（软退出自行收尾）。
- ``_compute_deadline`` 守卫：覆盖「停止先于首个测量请求发出」的窗口——此时
  deadline 尚未武装，跳变无效；守卫直接返回过去时刻，循环首轮即 break。
- duration 哨兵：数量驱动的任务不传 --duration（deadline 恒 None，跳变无从
  命中），wrapper 补一个比跳变量小的哨兵时长（仍远大于真实跑批时长，先到
  为准语义不变），保证任何任务都有可触发的 deadline。

跳变量（1e6 秒）远大于任何现实时长、又小于哨兵时长不会反向命中，两个常数
比值关系见常量注释。停止请求经 stop 文件传递（aite 监督循环写入），watcher
线程轮询读取。wrapper 自身退出码：软退出 0（perf_adapter 视为正常完成），
其余异常原样上抛。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

STOP_POLL_INTERVAL_S = 0.5
# 时钟跳变量：必须 > SENDINEL_DURATION（跳变后要命中「start + 哨兵」的 deadline），
# 又在现实跑批时长（< 数小时）之上若干数量级
STOP_CLOCK_JUMP_S = 1.0e6
# duration 哨兵：数量驱动任务补的 --duration（约 27.8 小时，先到为准下永不命中，
# 仅作为软退出的触发载体）；必须 < STOP_CLOCK_JUMP_S
DURATION_SENTINEL_S = 1.0e5

STATE = {"requested": False, "clocks": []}


class GracefulStopRequested(Exception):
    """停止请求后不再发起新档位/新轮次的入口守卫异常（wrapper 捕获后按 0 退出）。"""


class StopClock:
    """替身 ``time``：只代理 perf_counter，跳变整体前移（模块级替换，不影响
    http_client 等真实计时路径）。跳变写（watcher 线程）与读（事件循环线程）
    是 GIL 下的浮点赋值/读取，无需锁。"""

    def __init__(self) -> None:
        self.jump = 0.0

    def perf_counter(self) -> float:
        return time.perf_counter() + self.jump


def load_stop_request(path: str) -> dict | None:
    """读停止请求文件（不存在/写盘中途/非 JSON → None，下轮重读）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def trigger_stop() -> None:
    """时钟跳变 + 标记停止请求（幂等；watcher 线程与测试直接调用）。"""
    if STATE["requested"]:
        return
    STATE["requested"] = True
    for clock in STATE["clocks"]:
        clock.jump = STOP_CLOCK_JUMP_S


def _watch_stop_file(stop_file: str) -> None:
    while not STATE["requested"]:
        if load_stop_request(stop_file) is not None:
            trigger_stop()
            return
        time.sleep(STOP_POLL_INTERVAL_S)


def install(stop_file: str) -> None:
    """打补丁 + 启动 stop 文件 watcher（须在 run_perf_benchmark 之前调用）。"""
    import evalscope.perf.core.strategies.closed_loop as closed_loop
    import evalscope.perf.core.strategies.open_loop as open_loop
    import evalscope.perf.main as perf_main
    from evalscope.perf.core.strategies.base import BenchmarkStrategy

    clocks = [StopClock(), StopClock()]
    STATE["clocks"] = clocks
    closed_loop.time = clocks[0]        # type: ignore[assignment] 模块级替身
    open_loop.time = clocks[1]          # type: ignore[assignment]

    def guard_factory(orig):
        async def guarded(self, *args, **kwargs):
            if STATE["requested"]:
                raise GracefulStopRequested()
            return await orig(self, *args, **kwargs)
        return guarded

    closed_loop.ClosedLoopStrategy.run = guard_factory(closed_loop.ClosedLoopStrategy.run)
    open_loop.OpenLoopStrategy.run = guard_factory(open_loop.OpenLoopStrategy.run)

    orig_one = perf_main.run_one_benchmark

    def guarded_one(*args, **kwargs):
        if STATE["requested"]:
            raise GracefulStopRequested()
        return orig_one(*args, **kwargs)

    perf_main.run_one_benchmark = guarded_one

    orig_deadline = BenchmarkStrategy._compute_deadline   # 类属性访问即原函数

    def guarded_deadline(duration):
        # 停止先于武装的窗口：直接给过去时刻 → 派发循环首轮 break（排空为空）
        if STATE["requested"]:
            return time.perf_counter() - 1.0
        return orig_deadline(duration)

    BenchmarkStrategy._compute_deadline = staticmethod(guarded_deadline)

    threading.Thread(target=_watch_stop_file, args=(stop_file,),
                     daemon=True, name="perf-stop-watcher").start()


def main(argv: list[str]) -> int:
    """wrapper 入口：argv[0]=stop 文件路径，其余为 evalscope perf 参数。"""
    stop_file, rest = argv[0], list(argv[1:])
    if rest and rest[0] == "perf":      # aite 组装的首 token（CLI 子命令名）
        rest = rest[1:]
    sys.argv = ["evalscope", *rest]     # parse_args() 读 sys.argv[1:]

    install(stop_file)
    from evalscope.perf.arguments import parse_args
    from evalscope.perf.main import run_perf_benchmark

    args = parse_args()
    if getattr(args, "duration", None) is None:
        args.duration = DURATION_SENTINEL_S
    try:
        run_perf_benchmark(args)
    except GracefulStopRequested:
        print(f"[perf-wrapper] {time.strftime('%H:%M:%S')} GRACEFUL_STOP_COMPLETED "
              f"(dispatch stopped, in-flight drained, further levels skipped)",
              flush=True)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
