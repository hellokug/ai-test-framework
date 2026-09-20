"""可靠性复合任务框架单测（D2）：背景流量编排 / 注入窗对齐 / 结果合并。

run_with_chaos / run_observation / run_tool_suite 以 fake 替换，验证编排语义
而非真实注入（真实链路由实机 E2E 覆盖）。注入窗 = 背景流量时长（契约 §12.4）。
"""

import time

import pytest

from aite.core.taskkit import DomainTaskKit, TaskRunContext
from aite.service import reliability_tasks as rt
from aite.storage.models import ReliabilityTaskRow
from aite.web.events import EventBus


def make_ctx(repo, spec):
    kit = DomainTaskKit("reliability", ReliabilityTaskRow,
                        rt.ObserveTaskExecutor(), EventBus())
    return TaskRunContext(kit, "t1", spec, repo)


@pytest.fixture
def fakes(monkeypatch):
    calls = {}

    async def fake_chaos(specs, business):
        calls["specs"] = specs
        result = await business()
        return result, [{"fault": "kill", "phase": "injected", "at": time.time()}]

    async def fake_obs(cfg):
        calls.setdefault("probe_durations", []).append(cfg.duration)
        return [{"ok": 1, "t": time.time(), "ttft_ms": 10.0}]

    async def fake_suite(raw, repo):
        from aite.core.types import RunReport

        calls["bg_raw"] = raw
        return RunReport(run_name=raw["run_name"], run_id="bg-run-1",
                         status="passed", total_cases=6)

    monkeypatch.setattr(rt, "run_with_chaos", fake_chaos)
    monkeypatch.setattr(rt, "run_observation", fake_obs)
    monkeypatch.setattr("aite.tools.run_tool_suite", fake_suite)
    return calls


async def test_composite_orchestration(repo, fakes):
    """dataset_ref 解析进 perf.dataset；注入窗对齐背景时长；观测与背景概要合并。"""
    from aite.datasets.catalog import register_entry

    await register_entry(repo, dataset_type="perf", name="bg-ds",
                         source="generated", path="perf_datasets/bg-ds.jsonl",
                         version="seed7", stats={"count": 50})
    spec = {
        "target": {"base_url": "http://x:1/v1", "model": "m"},
        "faults": [],
        "interval": 2.0,
        "background": {"engine": "locust", "dataset_ref": "bg-ds",
                       "run_name": "bg-1",
                       "perf": {"duration": 120, "max_tokens": 64}},
    }
    result = await rt.ObserveTaskExecutor().execute(make_ctx(repo, spec))

    raw = fakes["bg_raw"]
    assert raw["engine"] == "locust" and raw["run_name"] == "bg-1"
    assert raw["target"]["type"] == "openai_compatible"
    assert raw["perf"]["dataset"] == "perf_datasets/bg-ds.jsonl"
    assert raw["perf"]["mode"] == "baseline" and raw["perf"]["stream"] is True
    # 注入窗对齐：探针窗口 = 背景时长；补测窗口 = interval * 3
    assert fakes["probe_durations"] == [120.0, 6.0]
    # 结果合并：观测时间线 + 注入事件 + 背景套件概要
    assert result["summary"]["total"] == 2
    assert result["events"][0]["fault"] == "kill"
    assert result["background"]["run_id"] == "bg-run-1"
    assert result["background"]["status"] == "passed"
    assert result["probe"]["duration"] == 120.0
    assert result["probe"]["background"] is True


async def test_composite_requires_duration(repo, fakes):
    spec = {"target": {"base_url": "http://x:1/v1", "model": "m"},
            "background": {"perf": {"prompt": "hi"}}}
    with pytest.raises(ValueError, match="duration"):
        await rt.ObserveTaskExecutor().execute(make_ctx(repo, spec))


async def test_composite_dataset_ref_must_be_perf(repo, fakes):
    from aite.datasets.catalog import register_entry

    await register_entry(repo, dataset_type="accuracy", name="acc-ds",
                         source="local", path="a.jsonl")
    spec = {"target": {"base_url": "http://x:1/v1", "model": "m"},
            "background": {"dataset_ref": "accuracy:acc-ds",
                           "perf": {"duration": 10}}}
    with pytest.raises(ValueError, match="perf 型"):
        await rt.ObserveTaskExecutor().execute(make_ctx(repo, spec))


async def test_composite_requires_payload_source(repo, fakes):
    spec = {"target": {"base_url": "http://x:1/v1", "model": "m"},
            "background": {"perf": {"duration": 10}}}
    with pytest.raises(ValueError, match="dataset"):
        await rt.ObserveTaskExecutor().execute(make_ctx(repo, spec))


async def test_observe_only_path_unchanged(repo):
    """无 background 键 = 纯观测（既有语义不回归）。"""
    body = {"target": {"base_url": "http://x:1/v1", "model": "m"},
            "faults": [], "duration": 1, "interval": 1}
    specs, cfg = rt.build_observe_cfg(body)
    assert cfg.duration == 1.0 and specs == []
