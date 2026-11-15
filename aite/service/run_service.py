"""编排 service 层：CLI 与 Web 共享的 suite 解析/路由/执行入口。

职责（CLAUDE.md 原则 1/3）：engine 路由判断、suite 校验、limit 透传改写、
存储 URL 提取此前在 cli/main.py 与 web/routers/runs.py 各持一份，现收口于此。
API 路由只做参数校验与 bg_task 包装，业务编排一律调本层。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aite import config
from aite.core.engine import ExecutionEngine
from aite.core.loader import load_suite
from aite.core.types import RunReport


@dataclass
class SuiteRequest:
    """解析后的 suite 请求：工具域 config=None（raw 即配置），自研域 config 为强类型。"""

    raw: dict
    is_tool: bool
    config: Any | None   # SuiteConfig（自研域）；工具域 None
    run_name: str


def resolve_suite(raw: Any) -> SuiteRequest:
    """engine 路由 + 校验（fail-fast）：工具域走 parse_tool_suite 轻校验，
    自研域走 load_suite 强校验（SuiteConfig，extra=forbid 不认 engine 字段）。"""
    if not isinstance(raw, dict):
        raise ValueError("suite config must be a mapping")
    if raw.get("engine"):
        from aite.tools import parse_tool_suite

        parse_tool_suite(raw)
        return SuiteRequest(raw=raw, is_tool=True, config=None,
                            run_name=str(raw.get("run_name") or "tool-run"))
    cfg = load_suite(raw)
    return SuiteRequest(raw=raw, is_tool=False, config=cfg, run_name=cfg.run_name)


def storage_url_of(raw: dict) -> str:
    """suite 内 storage.url，缺省走全局配置。"""
    return (raw.get("storage") or {}).get("url") or config.DEFAULT_DB_URL


def apply_tool_limit(raw: dict, limit: int | None) -> None:
    """CLI/Web 的 limit 参数 → 工具域 limit_per_subset 透传。"""
    if limit:
        raw.setdefault("tools", {}).setdefault("limit_per_subset", limit)


def apply_tool_resume(raw: dict, resume: str | bool | None) -> None:
    """CLI/Web 的 resume 参数 → 工具域透传（None = 用 suite yaml 值/缺省 auto）。

    档位语义归一与校验在 parse_tool_suite 的 normalize_resume_mode（auto/
    true/false），错误值在此直接 ValueError fail-fast。
    """
    if resume is None:
        return
    raw.setdefault("tools", {})["resume"] = resume


async def execute_suite(
    raw: dict,
    repo: Any,
    *,
    limit: int | None = None,
    dataset_ids: list[str] | None = None,
    progress: Any | None = None,
    event_cb: Callable[[dict], None] | None = None,
    concurrency: int | None = None,
    max_cost_usd: float | None = None,
    report_formats: str | None = None,
    chaos_raw: list[dict] | None = None,
    resume: str | bool | None = None,
    run_cb: Any = None,
    control: Any | None = None,
) -> RunReport:
    """统一执行入口：engine 路由 → 工具适配层 / 自研引擎。

    chaos_raw 非空时（工具域）故障注入包裹业务执行，注入事件写入
    run details.chaos_events（I5 业务观测时间线）。resume（工具域断点续推
    档位 auto/true/false）仅工具域生效，None = 用 yaml 值/缺省 auto。
    run_cb：run 行建好即回调 run_id（域任务记执行历史轮用，引擎/工具两路都接）。
    control：域任务停止通道（ToolRunControl，立即/强制停止事件），仅工具域
    子进程型适配器消费（perf 监督循环）；CLI 直跑为 None。
    """
    sreq = resolve_suite(raw)
    if sreq.is_tool:
        apply_tool_limit(raw, limit)
        apply_tool_resume(raw, resume)
        from aite.tools import run_tool_suite

        if not chaos_raw:
            return await run_tool_suite(raw, repo, event_cb=event_cb,
                                        run_cb=run_cb, control=control)

        from aite.chaos import run_with_chaos
        from aite.tools import execute_tool, persist_tool_result

        async def _business():
            result, _snap, req = await execute_tool(raw, control=control)
            return result, req

        (result, req), events = await run_with_chaos(chaos_raw, _business)
        return await persist_tool_result(
            raw, repo, result, req.work_dir,
            extra_details={"chaos_events": events})

    suite = sreq.config
    if concurrency:
        suite.concurrency = concurrency
    if max_cost_usd is not None:
        suite.max_cost_usd = max_cost_usd
    if report_formats:
        suite.report = [f.strip() for f in report_formats.split(",") if f.strip()]
    engine = ExecutionEngine(repo, progress=progress, event_cb=event_cb)
    return await engine.run_suite(suite, limit=limit, dataset_ids=dataset_ids,
                                  run_cb=run_cb)


async def close_interrupted_runs(repo: Any) -> int:
    """启动收敛（原则 8 状态机闭合）：单写者进程重启后，running 态的 run
    不可能还活着（后台任务由 mark_stale_bg_tasks 关账，run 行此前无人收）——
    统一关账为 error。返回收敛条数。"""
    runs = await repo.list_runs(limit=1000)
    stale = [r for r in runs if r.get("status") == "running"]
    for r in stale:
        await repo.update_run(r["id"], status="error",
                              error="interrupted: server restarted")
    return len(stale)
