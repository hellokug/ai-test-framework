"""可靠性 chaos 域任务装配（domain-backends.md）：故障注入 + 背景推理观测。

三种任务形态（同一执行器按 spec 分派）：
- **纯观测**：spec 与 POST /api/v1/chaos/observe 的 body 同构——
  {target, faults, interval?, duration?, prompt?, post_window?}；
  注入 → 探针观测 → 恢复 → 补测窗口 → RTO。
- **复合任务**（§12.4，D2）：多一个 `background` 键——性能数据集/负载参数铺
  背景流量，**注入窗 = 背景流量时长**，探针同窗观测；恢复后补测，RTO 判定。
  背景流量即 perf 套件（engine: locust/evalscope-perf），结果落公共 run 存储，
  概要并入任务 result.background。
- **实验套件**（§14.0 v3，E 系列）：{environment?, background?, probe?,
  defaults?, cases[]}——背景流量一次拉起全程持续，探针全程在线，用例顺序
  执行（每用例 target 发现层声明 + inject 注入层活体解析 + fault +
  expect_rto）；result.cases[] 每用例 rto/verdict。

结果内联任务表 result（时间线 + RTO + 背景概要，量小不另建明细表）。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from typing import Any

from aite.chaos.controller import run_with_chaos
from aite.chaos.model import FaultSpec, validate_specs
from aite.chaos.observer import (
    ProbeConfig,
    compute_rto,
    observe_under_chaos,
    run_observation,
    summarize_probes,
)
from aite.chaos.selectors import parse_process_spec, resolve_processes
from aite.core.taskkit import DomainTaskKit, EventSink, TaskExecutor, TaskRunContext
from aite.datasets.catalog import resolve_dataset_ref
from aite.events import EventBus
from aite.service.suite_task import suite_summary
from aite.storage.models import ReliabilityTaskRow

_MAX_OBSERVE_DURATION = 600.0      # 纯观测时长上限（秒），与同步 REST 端点同口径
_MAX_BACKGROUND_DURATION = 3600.0  # 复合任务背景流量时长上限（秒）


def build_observe_cfg(body: dict) -> tuple[list, ProbeConfig]:
    """spec body → (FaultSpec 列表, ProbeConfig)；校验失败抛 ValueError。"""
    target = body.get("target") or {}
    base_url = str(target.get("base_url") or "").strip().rstrip("/")
    model = str(target.get("model") or "").strip()
    if not base_url or not model:
        raise ValueError("target.base_url 与 target.model 必填")
    url = base_url if base_url.endswith("/chat/completions") \
        else base_url + "/chat/completions"
    specs = validate_specs(body.get("faults") or [])
    interval = float(body.get("interval") or 5.0)
    duration = float(body.get("duration") or 60.0)
    if interval <= 0 or duration <= 0 or duration > _MAX_OBSERVE_DURATION:
        raise ValueError(f"interval 须 >0；duration 须在 (0, "
                         f"{_MAX_OBSERVE_DURATION:.0f}] 秒内")
    api_key = os.environ.get(target.get("api_key_env") or "OPENAI_API_KEY", "EMPTY")
    timeout = float(body.get("timeout") or 30.0)
    if timeout <= 0:
        raise ValueError("timeout 须 >0")
    cfg = ProbeConfig(url=url, model=model, api_key=api_key,
                      interval=interval, duration=duration,
                      prompt=str(body.get("prompt") or "ping"), timeout=timeout)
    return specs, cfg


async def build_background_suite(bg: dict, body: dict, repo: Any) -> dict:
    """background 段 → perf 套件 raw（target 继承顶层；dataset_ref 解析为路径）。"""
    if not isinstance(bg, dict) or not bg:
        raise ValueError("background 须为对象（engine/dataset_ref/run_name/perf）")
    engine = str(bg.get("engine") or "locust")
    if engine not in ("locust", "evalscope-perf"):
        raise ValueError(
            f"background.engine 仅支持 locust/evalscope-perf，当前 {engine!r}")
    perf = dict(bg.get("perf") or {})
    duration = float(perf.get("duration") or 0)
    if duration <= 0:
        raise ValueError("background.perf.duration 必配（注入窗 = 背景流量时长）")
    if duration > _MAX_BACKGROUND_DURATION:
        raise ValueError(
            f"background.perf.duration 上限 {_MAX_BACKGROUND_DURATION:.0f} 秒")
    if bg.get("dataset_ref"):
        row = await resolve_dataset_ref(repo, str(bg["dataset_ref"]))
        if row["type"] != "perf":
            raise ValueError("background.dataset_ref 须引用 perf 型数据集，"
                             f"当前 {row['type']!r}")
        perf["dataset"] = row["path"]
    if not perf.get("dataset") and not perf.get("prompt"):
        raise ValueError("background.perf 须配 dataset/dataset_ref 或 prompt 之一")
    perf.setdefault("mode", "baseline")
    perf.setdefault("stream", True)
    target = dict(body.get("target") or {})
    target.setdefault("type", "openai_compatible")
    target["stream"] = bool(perf.get("stream"))
    run_name = str(bg.get("run_name")
                   or f"reliability-bg-{time.strftime('%Y%m%d-%H%M%S')}")
    return {"engine": engine, "run_name": run_name,
            "target": target, "perf": perf}


class ObserveTaskExecutor(TaskExecutor):
    """观测/复合执行器：无 background 键 = 纯观测；有 = 复合任务
    （背景流量铺满注入窗 + 探针同窗观测 → 恢复 → 补测 → RTO）。"""

    async def execute(self, ctx: TaskRunContext) -> dict:
        if ctx.spec.get("cases"):
            return await run_experiment_suite(ctx.spec, ctx)
        if ctx.spec.get("background"):
            return await run_composite(ctx.spec, ctx)
        return await run_observe(ctx.spec)


async def run_observe(body: dict) -> dict:
    """纯观测：body → 校验 → 观测编排 → 附 probe 概要（REST 与任务共用）。"""
    specs, cfg = build_observe_cfg(body)
    result = await observe_under_chaos(
        specs, cfg, post_window=body.get("post_window"))
    result["probe"] = {"url": cfg.url, "model": cfg.model,
                       "interval": cfg.interval, "duration": cfg.duration}
    return result


async def run_composite(body: dict, ctx: TaskRunContext) -> dict:
    """复合任务：注入 → 背景流量+探针并发跑满注入窗 → 恢复 → 补测 → RTO。

    编排语义（run_with_chaos：注入全部并验证 → 业务 → finally 逆序恢复）：
    业务槽 = 背景流量（perf 套件，落公共 run 存储）与探针观测并发；探针窗口
    自动对齐背景流量时长（注入窗 = 背景时长），恢复后补测窗口捕捉首个 pass。
    """
    from aite.tools import run_tool_suite

    specs, cfg = build_observe_cfg(body)
    bg_raw = await build_background_suite(body.get("background") or {}, body,
                                          ctx.repo)
    bg_duration = float((bg_raw.get("perf") or {}).get("duration"))
    probe_cfg = replace(cfg, duration=bg_duration)

    async def business():
        load = asyncio.create_task(run_tool_suite(bg_raw, ctx.repo))
        probes = await run_observation(probe_cfg)
        report = await load          # 背景流量收尾后注入才恢复（防泄漏语义）
        return probes, report

    (probes, report), events = await run_with_chaos(specs, business)
    post_window = body.get("post_window")
    post_window = cfg.interval * 3 if post_window is None else float(post_window)
    post = await run_observation(replace(cfg, duration=post_window))
    all_probes = probes + post
    return {
        "events": events,
        "probes": all_probes,
        "rto": compute_rto(all_probes, events),
        "summary": summarize_probes(all_probes),
        "probe": {"url": cfg.url, "model": cfg.model, "interval": cfg.interval,
                  "duration": bg_duration, "background": True},
        "background": suite_summary(report),
    }


# ------------------------------------------------- 实验套件（§14.0 v3，E 系列）

def _parse_suite(body: dict) -> tuple[list[dict], dict, dict | None, dict, float]:
    """套件 spec 预检 → (cases, defaults, background, probe, business_window)。"""
    if body.get("environment"):
        raise ValueError("environment 引用需环境纳管通道（R 系列）——"
                         "当前仅本机 host 环境（省略该键）")
    cases = body.get("cases") or []
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases 须为非空用例数组")
    names = [str(c.get("name") or "") for c in cases]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise ValueError("cases 每用例须有非空且不重复的 name")
    defaults = body.get("defaults") or {}
    bg = body.get("background") or None
    probe = body.get("probe") or {}
    base_url = str(probe.get("base_url") or "").strip().rstrip("/")
    model = str(probe.get("model") or "").strip()
    if not base_url or not model:
        raise ValueError("probe.base_url 与 probe.model 必填"
                         "（探针与背景流量同源此端点）")
    interval = float(probe.get("interval") or 5.0)
    timeout = float(probe.get("timeout") or 30.0)
    if interval <= 0 or timeout <= 0:
        raise ValueError("probe.interval/timeout 须 >0")
    if bg and not isinstance(bg, dict):
        raise ValueError("background 须为对象")
    business_window = float(
        (bg.get("perf") or {}).get("duration") if bg else body.get("duration"))
    if business_window <= 0:
        raise ValueError("业务窗时长：有 background 取 perf.duration，"
                         "否则 body.duration 必配且 >0")
    if business_window > _MAX_BACKGROUND_DURATION:
        raise ValueError(f"业务窗上限 {_MAX_BACKGROUND_DURATION:.0f} 秒")

    # 预检：逐用例 fault_duration 可解析且总耗时（fd+post）不超过业务窗
    post_window = float(probe.get("post_window") or interval * 3)
    total = 0.0
    for case in cases:
        fault = dict(case.get("fault") or {})
        fd = fault.get("fault_duration")
        fd = float(fd if fd is not None
                   else (defaults.get("fault_duration") or 0))
        if fd <= 0:
            raise ValueError(f"用例 {case['name']!r}: fault_duration 须在用例或 "
                             "defaults 指定且 >0（无恢复点则 RTO 无从判定）")
        total += fd + post_window
    if total > business_window:
        raise ValueError(f"用例总耗时（Σ fault_duration+post_window = {total:.0f}s）"
                         f"超过业务窗 {business_window:.0f}s——拉长 background/duration")
    return cases, defaults, bg, probe, business_window


def _resolve_case_fd(case: dict, defaults: dict) -> float:
    fault = dict(case.get("fault") or {})
    fd = fault.get("fault_duration")
    return float(fd if fd is not None
                 else (defaults.get("fault_duration") or 0))


async def run_experiment_suite(body: dict, ctx: TaskRunContext) -> dict:
    """实验套件编排（§14.0 v3）：背景流量一次拉起 + 探针全程在线 + 用例顺序执行。

    每用例：发现层 target 声明对象 → 注入层 inject 在**注入时刻活体解析**
    注入单元（match/exclude/select）→ 逐 pid 注入并确认 → fault_duration 到点
    定时恢复（背景持续）→ 探针捕捉恢复点 → RTO → verdict（expect_rto）。
    两层审计（命中 pod 对象集 / 实际注入 pid 清单）落 result.cases。
    """
    from aite.tools import run_tool_suite

    cases, defaults, bg, probe, _window = _parse_suite(body)
    base_url = str(probe.get("base_url") or "").strip().rstrip("/")
    model = str(probe.get("model") or "").strip()
    url = base_url if base_url.endswith("/chat/completions") \
        else base_url + "/chat/completions"
    api_key = os.environ.get(probe.get("api_key_env") or "OPENAI_API_KEY", "EMPTY")
    interval = float(probe.get("interval") or 5.0)
    timeout = float(probe.get("timeout") or 30.0)
    post_window = float(probe.get("post_window") or interval * 3)
    prompt = str(probe.get("prompt") or "ping")
    seed = int(defaults.get("seed") or 0)
    default_select = (defaults.get("inject") or {}).get("select") or {}

    bg_raw = None
    if bg:
        bg_raw = await build_background_suite(
            bg, {"target": {"base_url": base_url, "model": model}}, ctx.repo)

    probes: list[dict] = []
    events: list[dict] = []
    case_results: list[dict] = []

    async def business() -> dict | None:
        load = asyncio.create_task(run_tool_suite(bg_raw, ctx.repo)) \
            if bg_raw else None
        for case in cases:
            entry, case_probes, case_events = await _execute_case(
                case, defaults, default_select, seed, url, model, api_key,
                interval, timeout, prompt, post_window, ctx.repo)
            for ev in case_events:
                ev["case"] = case["name"]
            events.extend(case_events)
            probes.extend(case_probes)
            case_results.append(entry)
        bg_report = suite_summary(await load) if load is not None else None
        return bg_report

    bg_report = await business()
    judged = [c for c in case_results if c.get("verdict")]
    suite_verdict = None
    if judged:
        suite_verdict = "passed" if all(
            c["verdict"] == "passed" for c in judged) else "failed"
    return {
        "background": bg_report,
        "probes": probes,
        "events": events,
        "cases": case_results,
        "verdict": {"status": suite_verdict} if suite_verdict else None,
        "probe": {"url": url, "model": model, "interval": interval,
                  "timeout": timeout, "post_window": post_window,
                  "background": bool(bg)},
    }


async def _execute_case(case: dict, defaults: dict, default_select: dict,
                        seed: int, url: str, model: str, api_key: str,
                        interval: float, timeout: float, prompt: str,
                        post_window: float, repo: Any) -> tuple[dict, list, list]:
    """单用例：注入层活体解析 → 逐 pid 注入 → fault_duration 定时恢复 → 判定。"""
    fault = dict(case.get("fault") or {})
    injector_name = str(fault.get("injector") or "process")
    kind = str(fault.get("kind") or "")
    if not kind:
        raise ValueError(f"用例 {case['name']!r}: fault.kind 必填")
    fd = _resolve_case_fd(case, defaults)
    inject = case.get("inject") or {}
    if injector_name != "process":
        raise ValueError(f"用例 {case['name']!r}: inject 两级对象选择当前"
                         f"仅支持 process 武器（{injector_name!r} 走 R 系列"
                         "环境通道）")
    if not isinstance(inject, dict) or not inject:
        raise ValueError(f"用例 {case['name']!r}: 缺 inject"
                         "（命中注入单元有歧义不静默）")
    process_spec = inject.get("process")
    if not process_spec:
        raise ValueError(f"用例 {case['name']!r}: 缺 inject.process"
                         "（本机 host 环境注入单元=进程）")
    match, exclude, select_spec = parse_process_spec(process_spec)
    select_spec = select_spec or dict(default_select)
    pids = resolve_processes(match, exclude, select_spec, seed=seed)

    specs = [FaultSpec(injector=injector_name, kind=kind,
                       target={"pid": p},
                       params=dict(fault.get("params") or {}),
                       name=f"{kind}@pid{p}") for p in pids]

    def observe_case_window(fd=fd, post=post_window):
        return run_observation(replace(
            ProbeConfig(url=url, model=model, api_key=api_key,
                        interval=interval, timeout=timeout, prompt=prompt),
            duration=fd + post))

    case_probes, case_events = await run_with_chaos(
        specs, observe_case_window, fault_duration=fd)
    rto = compute_rto(case_probes, case_events)
    entry: dict[str, Any] = {
        "name": case["name"],
        "targets": [f"pid:{p}" for p in pids],
        "fault": {"injector": injector_name, "kind": kind,
                  "fault_duration": fd},
        "rto": rto,
    }
    expect = case.get("expect_rto")
    if expect is not None:
        ok = rto["rto_seconds"] is not None and rto["rto_seconds"] <= float(expect)
        entry["verdict"] = "passed" if ok else "failed"
        entry["checks"] = [{"name": "rto", "expected": f"<={expect}",
                            "actual": rto["rto_seconds"], "passed": ok}]
    return entry, case_probes, case_events


def build_reliability_task_kit(bus: EventSink | None = None) -> DomainTaskKit:
    return DomainTaskKit("reliability", ReliabilityTaskRow, ObserveTaskExecutor(),
                         bus or EventBus())
