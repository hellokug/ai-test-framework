"""实验套件编排单测（§14.0 v3 / E 系列）：两级对象选择 + fault_duration 定时恢复
+ 逐用例 verdict——真子进程实弹，探针以真实进程状态采样（不 mock 注入与解析）。"""

import asyncio
import subprocess
import sys
import time

import psutil
import pytest

from aite.service import reliability_tasks as rt
from aite.service.reliability_tasks import run_experiment_suite

TOKEN = "chaos-experiment-demo"


BASE_PY = getattr(sys, "_base_executable", sys.executable)
# venv 的 python.exe 是启动器壳，会再拉起基础解释器——psutil 里一变二，
# 直接用基础解释器生成子进程保证一一对应。

def _spawn(token: str) -> subprocess.Popen:
    return subprocess.Popen(
        [BASE_PY, "-c", f"import time;time.sleep(60)  #{token}"])


@pytest.fixture
def two_children():
    procs = [_spawn(f"{TOKEN}-a"), _spawn(f"{TOKEN}-b")]
    yield procs
    for p in procs:
        if p.poll() is None:
            p.terminate()


def _suite(watched: list[int]) -> dict:
    return {
        "duration": 6,
        "probe": {"base_url": "http://127.0.0.1:9/v1", "model": "m",
                  "interval": 0.05, "timeout": 0.2, "post_window": 0.4},
        "defaults": {"fault_duration": 0.5,
                     "inject": {"select": {"mode": "all"}}},
        "cases": [{"name": "suspend-all",
                   "inject": {"process": {"contains": TOKEN}},
                   "fault": {"injector": "process", "kind": "suspend"},
                   "expect_rto": 2}]}


async def test_experiment_suite_two_stage_and_verdict(two_children, monkeypatch):
    """两级解析（host 环境 Stage A 退化 / 进程 match）+ fault_duration 定时恢复
    （恢复发生在业务中途）+ expect_rto 判定。探针采样真实子进程状态：
    挂起窗内 fail、恢复后 pass。"""
    watched = [p.pid for p in two_children]

    async def status_probe(cfg):
        probes = []
        deadline = time.monotonic() + cfg.duration
        while True:
            stopped = any(psutil.Process(p).status() == psutil.STATUS_STOPPED
                          for p in watched)
            probes.append({"t": time.time(), "ok": 0 if stopped else 1})
            if time.monotonic() + cfg.interval >= deadline:
                break
            await asyncio.sleep(cfg.interval)
        return probes

    monkeypatch.setattr(rt, "run_observation", status_probe)
    result = await run_experiment_suite(_suite(watched), ctx=None)

    case = result["cases"][0]
    assert sorted(case["targets"]) == sorted(f"pid:{p}" for p in watched)
    assert case["verdict"] == "passed"
    assert 0 < case["rto"]["rto_seconds"] <= 2
    assert result["verdict"] == {"status": "passed"}
    assert all(ev.get("case") == "suspend-all" for ev in result["events"])
    # 定时恢复语义：恢复点在注入后约 fault_duration，远早于业务窗（6s）
    injected = next(e for e in result["events"] if e["phase"] == "injected")
    recovered = next(e for e in result["events"] if e["phase"] == "recovered")
    assert 0 < recovered["at"] - injected["at"] < 1.5


async def test_experiment_requires_fault_duration(two_children, monkeypatch):
    spec = _suite([p.pid for p in two_children])
    spec["defaults"] = {"inject": {"select": {"mode": "all"}}}
    spec["cases"][0]["fault"].pop("fault_duration", None)
    with pytest.raises(ValueError, match="fault_duration"):
        await run_experiment_suite(spec, ctx=None)


async def test_experiment_window_overflow_rejected(two_children):
    spec = _suite([p.pid for p in two_children])
    spec["duration"] = 0.5                               # < fd + post
    with pytest.raises(ValueError, match="超过业务窗"):
        await run_experiment_suite(spec, ctx=None)


async def test_experiment_ambiguous_without_inject(two_children):
    spec = _suite([p.pid for p in two_children])
    spec["cases"][0] = {"name": "no-inject",
                        "fault": {"injector": "process", "kind": "suspend"},
                        "expect_rto": 2}
    with pytest.raises(ValueError, match="缺 inject"):
        await run_experiment_suite(spec, ctx=None)


async def test_experiment_environment_channel_pending(two_children):
    spec = _suite([p.pid for p in two_children])
    spec["environment"] = "prod-1"
    with pytest.raises(ValueError, match="R 系列"):
        await run_experiment_suite(spec, ctx=None)
