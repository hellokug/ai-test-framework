"""工具适配层：外部评测工具的编排与结果统一入库。

suite yaml 带 ``engine: evalscope``（或其他注册工具名）时，``aite run`` 路由到
本层执行；结果写入 test_run / run_metric_summary，与自研引擎的结果在
Web 趋势中并列可比。无 engine 字段的 suite 走自研引擎，行为不变。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aite import config
from aite.core.types import MetricSummary, RunReport, utcnow
from aite.tools.base import (
    ToolAdapter as ToolAdapter,
)
from aite.tools.base import (
    ToolMetricRow as ToolMetricRow,
)
from aite.tools.base import (
    ToolRunControl as ToolRunControl,
)
from aite.tools.base import (
    ToolRunRequest as ToolRunRequest,
)
from aite.tools.base import (
    ToolRunResult as ToolRunResult,
)
from aite.tools.base import (
    get_tool,
)
from aite.tools.base import (
    list_tools as list_tools,
)
from aite.tools.base import (
    register_tool as register_tool,
)
from aite.tools.evalscope_adapter import EvalscopeAdapter  # noqa: F401  (registers)
from aite.tools.locust_adapter import LocustAdapter  # noqa: F401  (registers)
from aite.tools.perf_adapter import PerfAdapter  # noqa: F401  (registers)
from aite.tools.pytest_adapter import PytestAdapter  # noqa: F401  (registers)
from aite.tools.rageval_adapter import RagevalAdapter  # noqa: F401  (registers)
from aite.utils.logging import get_logger

logger = get_logger(__name__)


def build_overall_rows(rows: list[ToolMetricRow]) -> list[MetricSummary]:
    """工具域 overall 行（dataset=None）构建：与自研引擎口径对齐。

    CLI 汇总表只显示 overall 行，趋势页汇总视图也依赖它。多档/多数据集按
    count 加权平均；阈值方向取该 metric 组首行（同 metric 方向应一致，
    如 perf 的延迟类 <=）。
    """
    by_metric: dict[str, list[ToolMetricRow]] = {}
    for r in rows:
        by_metric.setdefault(r.metric, []).append(r)
    overall: list[MetricSummary] = []
    for metric, mrows in by_metric.items():
        total = sum(r.count for r in mrows)
        value = (sum(r.value * r.count for r in mrows) / total) if total else None
        thr = next((r.threshold for r in mrows if r.threshold is not None), None)
        higher = mrows[0].higher_is_better
        overall.append(MetricSummary(
            metric=metric, dataset=None, group=None, aggregation="mean",
            value=value, threshold=thr, count=total, pass_rate=None,
            higher_is_better=higher,
            passed=((value >= thr) if higher else (value <= thr))
            if (thr is not None and value is not None) else None,
        ))
    return overall


def serialize_summaries(rows: list[ToolMetricRow]) -> list[dict]:
    """指标行 → timeseries.json 的 ``summaries`` 形状（大屏统计表数据源）。

    overall 行（dataset=None，与 CLI 汇总表同源）在前、档级行在后；逐字段
    平铺为可 JSON 序列化的 dict，value/threshold/passed 允许 None（未配阈值
    → passed=None → 前端显示 "-"）。
    """

    def _dict(metric, dataset, value, count, threshold, passed, higher):
        return {"metric": metric, "dataset": dataset, "value": value,
                "count": count, "threshold": threshold, "passed": passed,
                "higher_is_better": higher}

    out = [_dict(r.metric, None, r.value, r.count, r.threshold, r.passed,
                 r.higher_is_better) for r in build_overall_rows(rows)]
    out += [_dict(r.metric, r.dataset, r.value, r.count, r.threshold,
                  r.passed, r.higher_is_better) for r in rows]
    return out


def parse_tool_suite(raw: dict) -> tuple[ToolRunRequest, dict]:
    """工具型 suite yaml → (ToolRunRequest, 原始配置快照)。

    入口边界先做 common 预制展开（target.profile 引用 + tools 环境缺省并入，
    幂等就地——resolve/execute/persist 多次进入本函数结果一致，快照自包含）。

    pytest 引擎（tools.tests_dir 模式）下 datasets 可空 = 跑该目录全部场景；
    evalscope-perf 引擎带 perf 段即可（datasets/tests_dir 均不要求）。
    """
    from aite.core.profiles import apply_common

    apply_common(raw)
    tools_cfg = raw.get("tools") or {}
    if isinstance(raw.get("target"), dict) and "judge" in raw["target"]:
        # 常见错置（实弹教训 2026-09-28）：judge 是工具域段级键，放 target 下
        # 会被静默忽略 → evalscope 报「judge.models must be provided」
        logger.warning("target 段出现 judge 键（已被忽略）：判官应配在 "
                       "tools.judge（evalscope）或 rageval.judge（RAGAS）")
    tests_dir = tools_cfg.get("tests_dir")
    perf_cfg = raw.get("perf")
    if perf_cfg is not None and not isinstance(perf_cfg, dict):
        raise ValueError("perf 段须为对象（prompt/parallel/number/...）")
    rageval_cfg = raw.get("rageval")
    if rageval_cfg is not None and not isinstance(rageval_cfg, dict):
        raise ValueError("rageval 段须为对象（metrics/judge/embeddings/language）")
    if not tools_cfg.get("datasets") and not tests_dir and not perf_cfg and not rageval_cfg:
        raise ValueError("tool suite requires tools.datasets (benchmark names)")
    run_name = raw.get("run_name") or "tool-run"
    stamp = utcnow().astimezone().strftime("%Y%m%d-%H%M%S")
    work_dir = (Path(raw.get("output_dir") or config.DEFAULT_REPORTS_DIR) / "tool"
                / str(raw["engine"]) / run_name / stamp)
    resume_mode = normalize_resume_mode(tools_cfg.get("resume"))
    resume = resume_mode in ("auto", "force")
    if resume:
        # 断点续推：evalscope use_cache 按 sample_id 复用预测/判分，只补缺失项；
        # 复用与否的决策矩阵（完成态 + 生成侧身份双闸）见 _apply_resume_policy。
        # 目录级缓存身份一致性另由 evalscope 侧校验兜底（不符自动降级 rerun_review）。
        work_dir = _apply_resume_policy(work_dir, resume_mode, raw)
    mt = tools_cfg.get("metric_thresholds")
    cases = tools_cfg.get("cases")
    if cases is not None and (not isinstance(cases, list)
                              or not all(isinstance(c, str) and c for c in cases)):
        raise ValueError("tools.cases 须为用例 id 字符串数组（省略 = 全部用例）")
    judge_cfg = tools_cfg.get("judge")
    if judge_cfg is not None and not isinstance(judge_cfg, dict):
        raise ValueError("tools.judge 须为对象（{profile: <common 预制>} 或内联 "
                         "{base_url/model/api_key_env}）")
    req = ToolRunRequest(
        run_name=run_name,
        target=raw.get("target") or {},
        datasets=list(tools_cfg.get("datasets") or []),
        limit_per_subset=tools_cfg.get("limit_per_subset"),
        work_dir=work_dir,
        extra=tools_cfg.get("extra_args") or {},
        metric_thresholds=mt if isinstance(mt, dict) else {},
        auto_thresholds=(mt == "auto"),
        tests_dir=tests_dir,
        cases=cases,
        perf=perf_cfg or {},
        rag_eval=rageval_cfg or {},
        judge=judge_cfg or {},
        resume=resume,
    )
    return req, raw


RESUME_STATE_FILE = "run_state.json"

# 只影响基础设施/判分路径、不改变“生成什么”的 extra_args 键——不在生成侧身份内
_RESUME_IDENTITY_EXCLUDED = frozenset(
    {"timeout_s", "python_exe", "eval_batch_size", "rerun_review"})


def normalize_resume_mode(value: Any) -> str:
    """tools.resume / CLI --resume / API resume 参数归一：auto（缺省）/ force / off。

    auto = 状态感知：上次没跑完 → 就地续推，跑完 → 全新起跑；YAML 布尔
    true/false 视为 force/off 等价（兼容历史 suite）。
    """
    if value is None or (isinstance(value, str) and value.lower() == "auto"):
        return "auto"
    if value is True or (isinstance(value, str) and value.lower() in ("true", "force")):
        return "force"
    if value is False or (isinstance(value, str) and value.lower() in ("false", "off")):
        return "off"
    raise ValueError(f"tools.resume 须为 auto / true / false，得到 {value!r}")


def tool_identity(raw: dict) -> dict:
    """工具域生成侧身份指纹：决定“预测长什么样”的字段集合。

    判官（tools.judge）与阈值/报告格式刻意不在内——判官可换（rerun_review
    就地重判，预测照旧复用）；模型/端点/生成参数/数据集/limit/数据侧 extra
    任一变更，旧预测即不可比，续推必须让位于全新起跑（如 limit 5 验管线后
    删掉 limit 跑全量 = 全新，不会把 5 题缓存混进正式成绩）。
    """
    tools_cfg = raw.get("tools") or {}
    target = {k: v for k, v in (raw.get("target") or {}).items()
              if k not in ("profile", "api_key", "api_key_env")}
    return {
        "engine": raw.get("engine"),
        "target": target,
        "datasets": list(tools_cfg.get("datasets") or []),
        "tests_dir": tools_cfg.get("tests_dir"),
        "limit_per_subset": tools_cfg.get("limit_per_subset"),
        "extra_args": {k: v for k, v in (tools_cfg.get("extra_args") or {}).items()
                       if k not in _RESUME_IDENTITY_EXCLUDED},
    }


def tool_identity_hash(raw: dict) -> str:
    return hashlib.sha256(
        json.dumps(tool_identity(raw), sort_keys=True, ensure_ascii=False)
        .encode("utf-8")).hexdigest()


def _read_run_state(work_dir: Path) -> dict | None:
    try:
        return json.loads((work_dir / RESUME_STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _resume_fresh_reason(mode: str, state: dict | None, ident_hash: str
                         ) -> str | None:
    """续推决策：返回“必须全新起跑”的原因，None = 允许复用最近目录。"""
    if state is None:
        # 旧版本目录/状态文件被删：force 照续（身份交 evalscope 校验），auto 不赌
        return None if mode == "force" else "历史目录无 run_state.json（旧版产物或被删除）"
    if state.get("identity_hash") != ident_hash:
        return "配置身份变更（模型/端点/数据集/limit/生成参数）"
    if state.get("status") == "completed":
        return None if mode == "force" else "上次运行已完成"
    return None


def _apply_resume_policy(default_dir: Path, resume_mode: str, raw: dict) -> Path:
    """断点续推目录决策：返回最终 work_dir（纯读盘决策，不写任何状态）。

    只看最近历史目录（时间戳名排序即时序）的 run_state.json：未完成且身份
    一致 → 就地续推；已完成 → 全新起跑（force 例外：改判官重判/重出报告）；
    身份不符（模型/端点/数据集/limit/生成参数变更）→ 一律全新，续推只会
    混入不可比的旧预测；无状态文件（旧版目录）→ force 照续（身份交 evalscope
    侧校验），auto 不赌。决策结果 logger.info 显式播报。
    """
    stamp_re = re.compile(r"^\d{8}-\d{6}$")
    candidates = sorted(d for d in default_dir.parent.iterdir()
                        if d.is_dir() and stamp_re.match(d.name)
                        ) if default_dir.parent.exists() else []
    latest = candidates[-1] if candidates else None
    if latest is None:
        logger.info("resume: 无历史输出目录，全新起跑")
        return default_dir
    state = _read_run_state(latest)
    fresh_reason = _resume_fresh_reason(resume_mode, state, tool_identity_hash(raw))
    if fresh_reason is not None:
        logger.info(f"resume={resume_mode}：{fresh_reason} → 全新起跑")
        return default_dir
    completed = bool(state) and state.get("status") == "completed"
    logger.info(f"断点续推：复用上次输出目录 {latest}（"
                + ("已完成：预测/判分全缓存，仅重出报告" if completed
                   else "只补缺失样本，已完成样本不重复计费") + "）")
    return latest


def _write_run_state(work_dir: Path, status: str, raw: dict) -> None:
    """run_state.json：断点续推的完成态 + 身份凭证（run_tool_suite 维护生命周期）。

    running 在执行前落盘（中断早于快照的目录身份不可知 → auto 下不复用），
    completed/failed 在收尾后落盘；failed 与 running 同为可续推态（余额不足/
    断电等中断后重跑只补缺失）。
    """
    identity = tool_identity(raw)
    payload = {"status": status, "identity_hash": tool_identity_hash(raw),
               "identity": identity, "updated_at": utcnow().isoformat()}
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / RESUME_STATE_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


async def execute_tool(raw: dict, work_dir: Path | None = None,
                       control: ToolRunControl | None = None
                       ) -> tuple[ToolRunResult, dict, ToolRunRequest]:
    """纯执行：构造请求 → 适配器执行 → 结果（不落库，agent 回传场景用）。

    work_dir 缺省走 parse_tool_suite 的 reports/tool/<engine>/<run>/<stamp>。
    control：域任务停止通道（立即/强制停止事件），CLI 直跑为 None。
    """
    engine_name = raw.get("engine")
    adapter = get_tool(engine_name)
    req, cfg_snapshot = parse_tool_suite(raw)
    if work_dir is not None:
        req.work_dir = Path(work_dir)
    req.control = control
    result = await adapter.run(req)
    return result, cfg_snapshot, req


def build_tool_report(raw: dict, repo: Any, result: ToolRunResult,
                      work_dir: Path) -> RunReport:
    """纯入库（同步部分）：ToolRunResult → RunReport 聚合与 details（不写库）。"""
    engine_name = raw.get("engine")
    req, cfg_snapshot = parse_tool_suite(raw)
    report = RunReport(
        run_name=req.run_name, pass_strategy="all_of", started_at=utcnow(),
        config=cfg_snapshot,
    )
    summaries = [
        MetricSummary(
            metric=r.metric, dataset=r.dataset, group=r.group,
            aggregation="mean", value=r.value, threshold=r.threshold,
            passed=r.passed, count=r.count, pass_rate=None,
            higher_is_better=r.higher_is_better,
        )
        for r in result.rows
    ]
    summaries.extend(build_overall_rows(result.rows))
    report.summaries = summaries
    n_samples = sum(int(v.get("summary", {}).get("n_samples", 0))
                    for v in result.perf.values())
    # total_cases 回退：首个 metric 组的 count 总和（多数据集/多场景求和，而非首行）
    first_metric = result.rows[0].metric if result.rows else None
    total_counts = sum(r.count for r in result.rows if r.metric == first_metric)
    report.total_cases = n_samples or total_counts
    failed = any(r.passed is False for r in result.rows)
    report.status = "failed" if failed else "passed"
    report.details["tool"] = engine_name
    report.details["reports"] = result.report_files
    report.details["raw_score"] = result.raw_score
    if result.failures:   # 用例级失败详情（CLI 打印，入库不含 details 无膨胀）
        report.details["failures"] = result.failures[:50]
    if result.perf:
        report.details["perf"] = result.perf
    report.details["work_dir"] = str(work_dir)
    report.finished_at = utcnow()
    _ = repo   # 签名对齐 persist_tool_result；此处不触库
    return report


async def persist_tool_result(raw: dict, repo: Any, result: ToolRunResult,
                              work_dir: Path, run_id: str | None = None,
                              extra_details: dict | None = None) -> RunReport:
    """纯入库（异步）：指标汇总落库 → 终态与 details 回写。

    master 回传场景与本地执行共用（agent 场景 work_dir 为 master 侧重建目录）；
    run_id 传入时复用已建 run 行（本地执行先建行保持 running 中间态可见）。
    """
    req, cfg_snapshot = parse_tool_suite(raw)
    report = RunReport(
        run_name=req.run_name, pass_strategy="all_of", started_at=utcnow(),
        config=cfg_snapshot,
    )
    suite_id = await repo.upsert_suite(req.run_name, cfg_snapshot)   # 幂等：用例快照挂靠需要
    if run_id is None:
        run_id = await repo.create_run(req.run_name, cfg_snapshot, suite_id)
    report.run_id = run_id

    summaries = [
        MetricSummary(
            metric=r.metric, dataset=r.dataset, group=r.group,
            aggregation="mean", value=r.value, threshold=r.threshold,
            passed=r.passed, count=r.count, pass_rate=None,
            higher_is_better=r.higher_is_better,
        )
        for r in result.rows
    ]
    summaries.extend(build_overall_rows(result.rows))
    report.summaries = summaries
    n_samples = sum(int(v.get("summary", {}).get("n_samples", 0))
                    for v in result.perf.values())
    first_metric = result.rows[0].metric if result.rows else None
    total_counts = sum(r.count for r in result.rows if r.metric == first_metric)
    report.total_cases = n_samples or total_counts
    failed = any(r.passed is False for r in result.rows)
    report.status = "failed" if failed else "passed"
    report.details["tool"] = raw.get("engine")
    report.details["reports"] = result.report_files
    report.details["raw_score"] = result.raw_score
    if result.failures:
        report.details["failures"] = result.failures[:50]
    if result.perf:
        report.details["perf"] = result.perf
    if extra_details:
        report.details.update(extra_details)
    report.finished_at = utcnow()

    for s in summaries:
        await repo.save_metric_summary(run_id, s)
    for rec in getattr(result, "case_records", None) or []:
        await repo.save_tool_case_record(run_id, suite_id, rec)
    # 平台报告渲染（与自研引擎同款）：suite report: 配置的格式落盘 work_dir
    from aite.reporting.renderers import render_report_formats

    written = render_report_formats(
        report, Path(work_dir), cfg_snapshot.get("report") or [])
    result.report_files.update(written)
    await repo.update_run(
        run_id, status=report.status,
        overall_score=next(iter(result.raw_score.values()), None),
        total_cases=report.total_cases,
        finished_at=report.finished_at,
        details={"reports": result.report_files, "work_dir": str(work_dir),
                 "failures": result.failures[:50]},
    )
    return report


async def _persist_run_failure(repo: Any, run_id: str, report: RunReport,
                               exc: Exception, raw: dict, req: ToolRunRequest) -> None:
    """run 级失败统一落库（run 行 error 态 + run_state.json failed 凭证）。"""
    logger.exception("tool run failed")
    report.status = "error"
    report.details["error"] = f"{type(exc).__name__}: {exc}"
    await repo.update_run(run_id, status="error", error=str(exc)[:2000],
                          finished_at=utcnow(),
                          details={"work_dir": str(req.work_dir)})
    try:
        _write_run_state(req.work_dir, "failed", raw)
    except OSError as write_exc:
        logger.warning("run_state.json 写盘失败（%s）：该目录将不可"
                       "断点续推（无完成态凭证）", write_exc)


async def run_tool_suite(raw: dict, repo: Any,
                         event_cb: Callable[[dict], None] | None = None,
                         run_cb: Any = None,
                         control: ToolRunControl | None = None,
                         ) -> RunReport:
    """本地执行工具型 suite：先建 run 行（running 可见）→ 执行 → 入库。

    event_cb：运行期进度事件出口（Web SSE / CLI 打印共用）；适配器经
    req.progress_cb 回传（如 evalscope 日志解析出的 done/total），total
    已知即回写 run 行 total_cases。run_cb：run 行建好即回调（域任务用它把
    run_id 记进执行历史轮，失败/取消轮也能对上留痕）。control：域任务
    停止通道（立即停止 cancel_event / 强制停止 force_event），适配器监督
    循环轮询响应；CLI 直跑为 None。"""
    engine_name = raw.get("engine")
    req, cfg_snapshot = parse_tool_suite(raw)
    report = RunReport(
        run_name=req.run_name, pass_strategy="all_of", started_at=utcnow(),
        config=cfg_snapshot,
    )
    suite_id = await repo.upsert_suite(req.run_name, cfg_snapshot)
    run_id = await repo.create_run(req.run_name, cfg_snapshot, suite_id)
    report.run_id = run_id
    if run_cb:
        await run_cb(run_id)
    last_total: int | None = None
    bg_updates: set[asyncio.Task] = set()

    def progress_cb(ev: dict) -> None:
        """适配器进度事件 → event_cb（SSE/CLI）+ 异步回写 run 行（total 首次已知
        时写 total_cases；details.progress 每次都写——前端轮询 run 行即可见，
        CLI 下发同样生效）。log 镜像事件只走展示通道（入 detail 会刷爆 run 行）。"""
        nonlocal last_total
        if ev.get("type") == "log":
            if event_cb:
                event_cb(ev)
            return
        if event_cb:
            event_cb(ev)
        total = ev.get("total")
        first_total = bool(total) and total != last_total
        if first_total:
            last_total = total

        async def _write():
            with contextlib.suppress(Exception):
                fields: dict[str, Any] = {"details": {"progress": ev}}
                if first_total:
                    fields["total_cases"] = int(total)
                await repo.update_run(run_id, **fields)

        task = asyncio.create_task(_write())
        bg_updates.add(task)
        task.add_done_callback(bg_updates.discard)

    req.progress_cb = progress_cb
    req.control = control
    _write_run_state(req.work_dir, "running", raw)
    try:
        adapter = get_tool(engine_name)
        result = await adapter.run(req)
        if bg_updates:
            await asyncio.gather(*bg_updates, return_exceptions=True)
        report = await persist_tool_result(raw, repo, result, req.work_dir,
                                           run_id=run_id)
        # completed 必须晚于 persist：persist 内部会再次 parse_tool_suite，
        # 提前写完成态会让那次解析把 auto 决策翻向全新目录。写盘失败不推翻
        # 已入库的成功结果，但必须告警——状态文件滞后会让下次 auto 白续推一趟
        try:
            _write_run_state(req.work_dir, "completed", raw)
        except OSError as exc:
            logger.warning("run_state.json 写盘失败（%s）：%s；下次 auto 会"
                           "重续推该目录（预测/判分全缓存，无害）", req.work_dir, exc)
        return report
    except Exception as exc:  # noqa: BLE001 - run-level failure must be persisted
        await _persist_run_failure(repo, run_id, report, exc, raw, req)
        return report
