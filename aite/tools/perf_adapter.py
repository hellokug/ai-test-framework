"""evalscope-perf 适配器（模块 E：性能测试执行引擎）。

suite yaml 写 ``engine: evalscope-perf`` 即路由到本适配器；压测委托给
evalscope perf 子命令（vLLM 社区同源的 LLM 压测方案：aiohttp + 多 worker，
TTFT/TPOT/ITL/吞吐原生分档），aite 负责编排与统一入库——不自研压测客户端
（单进程 asyncio 客户端在高并发下自身即瓶颈，测的是客户端排队不是服务端）。

**负载**（``dataset`` / ``prompt`` / ``synthetic`` 三选一）：

- ``dataset``：本地 jsonl/csv 文件，行数 = 条数，输入键同平台数据集约定
  （input/question/instruction/prompt/query/user 取首个命中）；适配器导出为
  逐行 prompt 文件（evalscope ``custom`` 数据集），打流时逐条取用
- ``prompt``：固定提示词
- ``synthetic``：合成负载 {count, prompt_length}——确定性生成固定输入
  （输入一致则输出分布一致，曲线可比）
- ``max_tokens``：标量 = 定长；``[min, max]`` = 每请求均匀采样（输出长度分布）。
  优先级：任务显式配置 > 平台数据集 meta（同名 ``.stats.json`` 的
  ``output_tokens``，任务未配时定长兜底，输出对齐场景设计）
- ``min/max_prompt_length`` + ``tokenizer_path``：按真实 token 数过滤输入

**生成/采样参数**（显式配置才上 CLI，留空 = 端点缺省）：``temperature`` /
``top_p`` / ``top_k`` / ``min_tokens`` / ``frequency_penalty`` /
``repetition_penalty`` / ``seed`` / ``stop``（标量或数组）/
``query_template``（请求体 JSON 模板，``messages`` 由负载填充，``@file``
引用文件）/ ``apply_chat_template``（bool；chat 与 completions 端点二选）。

**模式**（``perf.mode`` 四选一；省略 = 兼容直配 ``parallel``/``number``）：

- ``baseline``：固定并发基准 → 单值 ``--parallel``
- ``ramp``：爬坡（from/to/step 展开为并发梯度，``to`` 必含）→ 多值 ``--parallel``
- ``probe``：摸高（SLA 约束下二分搜索最大可持续并发）→ ``--sla-auto-tune``；
  ``sla: {指标: 上限}`` 全部按 ``<`` 组成 AND 组
- ``soak``：长稳（时间驱动）→ ``--parallel`` + ``--duration``

**时长**（``duration`` 与 ``level_duration`` 二选一）：

- ``duration``：整场压测总时长（秒）。单档模式（baseline/soak/直配单档）总时长
  即单档时长直传；多档梯度均分为每档秒数（近似预算，不含 warmup/档间开销）；
  probe 搜索轮数不可预知、无法均分 → 看门狗：到时杀子进程，收集已完成轮次
  （日志标注截断，不报错）
- ``level_duration``：每档固定秒数。多档直传；probe = 每轮墙钟上限（每轮采样
  数仍由 SLA 逻辑强制 = 并发×2）；单档模式与 duration 等价

采样：``warmup_num`` 预热不计入；``stream: true``（默认）才有真 TTFT；
``number``/``--duration`` 先到为准；时长驱动（配了任一时长键且未显式配
``number``）时 number 自动放大安全上限（防默认条数提前结束），否则缺省 8。
中途熔断上游不支持（SLA 档间判定），backlog。

**停止（两段式，任务域 ToolRunControl 通道）**：

- 立即停止（``cancel_event``）：监督循环写协停请求文件，wrapper（
  ``perf_wrapper.py``）触发策略软退出——停止派发新请求、排空在途后当前档
  正常收尾落盘，统计含排空返回的请求；排空超过 ``GRACEFUL_DRAIN_BUDGET_S``
  仍未返回（服务端挂死）→ 升级杀树按强制停止兜底。
- 强制停止（``force_event`` 或 ``perf.force_stop_minutes`` 到时）：监督循环
  taskkill 杀整棵进程树，在途请求即弃；已完成请求已由 evalscope 逐条流式
  写入 ``benchmark_data.db``，collect 对有库无 summary 的档位按库兜底合成
  指标（未返回请求天然不在库中 = 不计入统计），并打 ``_stop`` 截断标记。

collect 从各档 ``benchmark_summary.json`` / ``benchmark_percentile.json`` 提取
指标 → 每档每指标一行 ``dataset="perf:c<并发>"``；阈值方向区分越高/越低越好。
负载/模式/时长解析与 locust 适配器共用 ``perf_common``；子进程执行共用
``subproc``。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from aite.tools.base import (
    ToolAdapter,
    ToolRunControl,
    ToolRunRequest,
    ToolRunResult,
    api_endpoint,
    register_tool,
    run_sync_in_thread,
)
from aite.tools.perf_common import (
    DEFAULT_TIMEOUT,
    DURATION_DRIVEN_NUMBER_CAP,
    append_metric_rows,
    dataset_output_tokens,
    export_prompts,
    generate_synthetic_prompts,
    positive_ints,
    resolve_durations,
    resolve_load,
    resolve_mode_parallel,
    write_timeseries,
)
from aite.tools.subproc import (
    glob_report_files,
    kill_tree,
    which_or_fail,
)
from aite.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BUCKET_SECONDS = 5.0   # 时序聚合桶间隔（秒），perf.bucket_seconds 可覆盖
SLEEP_INTERVAL_BETWEEN_LEVELS = 5.0   # evalscope 多档 sweep 档间默认间隔（秒）
SUPERVISE_POLL_INTERVAL_S = 0.5      # 子进程监督循环轮询间隔（秒）
GRACEFUL_DRAIN_BUDGET_S = 600.0      # 立即停止排空在途的等待预算，超时升级杀树
STOP_FILE_NAME = "stop_request.json"   # 协停请求文件（监督循环写 / wrapper 轮询读）

# 停止结局（_supervise 返回 how）：exit=自然退出；timeout=整场预算耗尽；
# force=强制杀树（stop force / force_stop_minutes 到时 / 排空预算超时升级）
_HOW_EXIT, _HOW_TIMEOUT, _HOW_FORCE = "exit", "timeout", "force"

# evalscope SLA 可断言指标（sla_run.get_metric_values 键）；
# 单位：latency 秒、ttft/tpot 毫秒、rps/tps 原生单位
_SLA_METRICS = {"avg_latency", "avg_ttft", "avg_tpot", "p99_latency", "p99_ttft",
                "p99_tpot", "p50_ttft", "p90_ttft", "p50_tpot", "p90_tpot",
                "rps", "tps"}

# benchmark_summary.json → 入库指标映射（None 跳过）
_SUMMARY_METRICS = {
    "ttft_ms": "TTFT (ms)",
    "tpot_ms": "TPOT (ms)",
    "itl_ms": "ITL (ms)",
    "qps": "Req Throughput (req/s)",
    "output_tps": "Output Throughput (tok/s)",
    "avg_input_tokens": "Avg Input Tokens",
    "avg_output_tokens": "Avg Output Tokens",
}
# benchmark_percentile.json 分位行 → 入库多档位指标（秒→毫秒仅 Latency）；
# 行缺失（上游版本无该分位）→ None 跳过（宁缺毋错）
_PERCENTILE_METRICS = {
    f"{base}_{tier}_ms": (row, column, scale)
    for base, (column, scale) in {
        "ttft": ("TTFT (ms)", 1.0), "latency": ("Latency (s)", 1000.0),
        "tpot": ("TPOT (ms)", 1.0), "itl": ("ITL (ms)", 1.0)}.items()
    for tier, row in {"p50": "50%", "p90": "90%", "p95": "95%",
                      "p99": "99%"}.items()
}


def _sla_params_json(sla: dict) -> str:
    """{指标: 上限} → evalscope --sla-params JSON（单 AND 组，全部 <）。"""
    if not isinstance(sla, dict) or not sla:
        raise ValueError("perf.probe.sla 须为 {指标: 上限} 非空对象")
    unknown = set(sla) - _SLA_METRICS
    if unknown:
        raise ValueError(f"perf.probe.sla 未知指标 {sorted(unknown)}（可用: {sorted(_SLA_METRICS)}）")
    for v in sla.values():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ValueError("perf.probe.sla 的值须为正数（指标上限）")
    return json.dumps([{k: f"< {v}" for k, v in sla.items()}])


_TOKEN_TIMING_FLOOR_MS = 1.0   # ITL/TPOT 失真门槛：avg < 1ms = 读缓冲批量到达


def _gated(value: float | None) -> float | None:
    """token 时延失真门槛（宁缺毋错）：正常 LLM ITL/TPOT ≥ 数 ms。"""
    if value is None:
        return None
    return None if value < _TOKEN_TIMING_FLOOR_MS else value


def _quantile(sorted_vals: list[float], q: float) -> float | None:
    """线性插值分位数（与 numpy 默认插值一致）。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _db_level_max(db_path: Path) -> dict[str, float] | None:
    """单档 benchmark_data.db 逐请求记录 → 各时延指标最大值（毫秒）。

    分位文件无 max 档，最大值只能从逐请求记录取（成功请求；秒→毫秒）。
    db 缺失/列缺失（上游版本差异）→ None（宁缺毋错）。
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "SELECT latency, first_chunk_latency, time_per_output_token,"
            " inter_token_latencies FROM result WHERE success").fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not rows:
        return None
    maxes = {"latency": 0.0, "ttft": 0.0, "tpot": 0.0, "itl": 0.0}
    for latency, ttft, tpot, itl_json in rows:
        if latency is not None:
            maxes["latency"] = max(maxes["latency"], float(latency) * 1000.0)
        if ttft is not None:
            maxes["ttft"] = max(maxes["ttft"], float(ttft) * 1000.0)
        if tpot is not None:
            maxes["tpot"] = max(maxes["tpot"], float(tpot) * 1000.0)
        if itl_json:
            try:
                for iv in json.loads(itl_json):
                    maxes["itl"] = max(maxes["itl"], float(iv) * 1000.0)
            except ValueError:
                pass
    return maxes


def _aggregate_level_db(db_path: Path, bucket: float) -> tuple[list[dict], float] | None:
    """单档 benchmark_data.db → 桶级时序点（大屏曲线数据源）。

    逐请求记录（evalscope perf 子进程流式写入）事后聚合：按 completed_time 落桶，
    t = 桶起点相对该档首个请求开始的秒数。成功请求计 TTFT/延迟分位，失败计错误数；
    rps/tok_s = 桶内完成请求数 / completion_tokens 和 ÷ 桶时长。
    返回 (点列表, raw_t0)——raw_t0 为同源原始时钟（perf 进程相对秒），供 combined
    跨档拼接与 epoch 换算。
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "SELECT completed_time, latency, first_chunk_latency, success,"
            " completion_tokens, time_per_output_token, inter_token_latencies"
            " FROM result ORDER BY completed_time").fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not rows:
        return None
    t0 = rows[0][0]
    buckets: dict[int, dict] = {}
    for completed, latency, ttft, ok, tokens, tpot, itl_json in rows:
        idx = int(max((completed or 0) - t0, 0.0) // bucket)
        b = buckets.setdefault(idx, {"n": 0, "errors": 0, "tokens": 0.0,
                                     "ttft": [], "latency": [], "tpot": [],
                                     "itl": []})
        b["n"] += 1
        b["tokens"] += float(tokens or 0)
        if ok:
            if ttft is not None:
                b["ttft"].append(float(ttft) * 1000.0)   # 秒 → 毫秒
            if latency is not None:
                b["latency"].append(float(latency) * 1000.0)
            if tpot is not None:
                b["tpot"].append(float(tpot) * 1000.0)
            if itl_json:
                try:
                    for iv in json.loads(itl_json):
                        b["itl"].append(float(iv) * 1000.0)
                except ValueError:
                    pass
        else:
            b["errors"] += 1

    def _ms(vals: list[float], *, avg: bool) -> float | None:
        if not vals:
            return None
        return round((sum(vals) / len(vals)) if avg else _quantile(vals, 0.95), 1)

    series = []
    for idx in sorted(buckets):
        b = buckets[idx]
        series.append({
            "t": round(idx * bucket, 3),
            "ttft_avg": _ms(b["ttft"], avg=True),
            "ttft_p95": _ms(b["ttft"], avg=False),
            "latency_avg": _ms(b["latency"], avg=True),
            "latency_p95": _ms(b["latency"], avg=False),
            # tpot/itl 失真门槛：<1ms 为读缓冲批量到达（观测失真），置 None
            "tpot_avg": _gated(_ms(b["tpot"], avg=True)),
            "tpot_p95": _gated(_ms(b["tpot"], avg=False)),
            "itl_avg": _gated(_ms(b["itl"], avg=True)),
            "itl_p95": _gated(_ms(b["itl"], avg=False)),
            "rps": round(b["n"] / bucket, 3),
            "tok_s": round(b["tokens"] / bucket, 1),
            "errors": b["errors"],
        })
    return series, t0


def _resolve_rate(cfg: dict) -> list[float] | None:
    """QPS 模式（perf.rate，开环）→ rate 梯度；与 parallel/mode 互斥。

    开环语义（evalscope --open-loop --rate）：按 Poisson 到达率发射请求、并发
    无界——服务端拖慢时排队/过载真实显现（与闭环限速的 locust qps 相对）。
    rate 列表即梯度（替代 parallel sweep）。
    """
    rate = cfg.get("rate")
    if rate is None:
        return None
    if cfg.get("parallel"):
        raise ValueError("perf.rate（QPS 模式）与 perf.parallel 互斥——"
                         "并发梯度请用 perf.parallel")
    if cfg.get("mode"):
        raise ValueError("perf.rate（QPS 模式）与 perf.mode 互斥——rate 列表本身"
                         "即梯度（等价 ramp 的 QPS 版）")
    values = [rate] if isinstance(rate, (int, float)) and         not isinstance(rate, bool) else list(rate)
    if not values or not all(isinstance(v, (int, float))
                             and not isinstance(v, bool) and v > 0 for v in values):
        raise ValueError("perf.rate 须为 >0 的数字（标量或梯度数组，req/s）")
    return [float(v) for v in values]


def _probe_args(mode: str | None, probe_cfg: dict) -> list[str]:
    """probe 模式 → --sla-* 参数（SLA 校验见 _sla_params_json）。"""
    if mode != "probe":
        return []
    max_parallel = probe_cfg.get("max_parallel")
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 1:
        raise ValueError("perf.probe 需要 max_parallel（≥1 整数，搜索上界）")
    args = [
        "--sla-auto-tune", "--sla-variable", "parallel",
        "--sla-upper-bound", str(max_parallel),
        "--sla-params", _sla_params_json(probe_cfg.get("sla") or {}),
    ]
    if probe_cfg.get("min_parallel"):
        args += ["--sla-lower-bound", str(int(probe_cfg["min_parallel"]))]
    if probe_cfg.get("runs"):
        args += ["--sla-num-runs", str(int(probe_cfg["runs"]))]
    return args


def _resolve_number(cfg: dict, plan, n_levels: int,
                    open_loop: bool = False) -> list[int]:
    """number 采样：显式配 → 先到为准；时长/数量驱动且未配 → 放大安全上限。

    开环 QPS 模式也放大（缺省 8 条会让 rate 压测瞬间结束——QPS 模式的终止
    条件是时长或数量，两者都不设即数量驱动到安全上限）。
    """
    number = cfg.get("number")
    if number is None:
        number = (DURATION_DRIVEN_NUMBER_CAP
                  if (plan.duration_driven or open_loop) else 8)
    if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
        number = [number] * n_levels   # 标量自动对齐梯度长度
    if not positive_ints(number) or len(number) != n_levels:
        raise ValueError("perf.number 须为 ≥1 整数，或与 parallel 等长的数组"
                         "（evalscope 校验规则：两者等长）")
    return number


def _assemble_args(cfg: dict, *, url: str, model: str, api_key: str,
                   parallel: list[int], number: list[int], plan,
                   dataset: str, prompt: str, synthetic: dict | None,
                   output_dir: Path, probe_args: list[str],
                   rates: list[float] | None = None) -> list[str]:
    """解析结果 → evalscope perf CLI 参数（rates 非空 = 开环 QPS 模式）。"""
    args = [
        "perf",
        "--url", url, "--model", model, "--api", "openai",
        "--api-key", api_key,
    ]
    if rates is not None:
        # 开环：--rate sweep 替代 --parallel；number 与 rate 等长（evalscope 校验）
        args += ["--open-loop", "--rate", *[str(r) for r in rates]]
    else:
        args += ["--parallel", *[str(p) for p in parallel]]
    args += ["--number", *[str(n) for n in number],
             "--no-timestamp",
             "--outputs-dir", str(output_dir),
             "--stream" if cfg.get("stream", True) else "--no-stream"]
    if plan.per_level is not None:
        # 整数秒发整数（CLI 可读性：30 而非 30.0）
        dur = plan.per_level
        args += ["--duration", str(int(dur) if dur == int(dur) else dur)]
    if dataset or synthetic:
        # dataset 由 run 阶段导出 / synthetic 由 run 阶段生成，统一走 custom 数据集
        args += ["--dataset", "custom", "--dataset-path",
                 str(output_dir.parent / "load" / "prompts.txt")]
    else:
        args += ["--prompt", prompt]
    max_tokens = cfg.get("max_tokens")
    if isinstance(max_tokens, list):
        if (len(max_tokens) != 2 or not all(
                isinstance(v, int) and not isinstance(v, bool) and v >= 1
                for v in max_tokens) or max_tokens[0] > max_tokens[1]):
            raise ValueError("perf.max_tokens 区间须为 [min, max]（≥1 整数，min ≤ max）")
        args += ["--max-tokens", str(max_tokens[0]), str(max_tokens[1])]
    elif max_tokens:
        args += ["--max-tokens", str(int(max_tokens))]
    if cfg.get("warmup_num"):
        args += ["--warmup-num", str(int(cfg["warmup_num"]))]
    if cfg.get("tokenizer_path"):
        args += ["--tokenizer-path", str(cfg["tokenizer_path"])]
    for key, flag in (("min_prompt_length", "--min-prompt-length"),
                      ("max_prompt_length", "--max-prompt-length")):
        if cfg.get(key):
            args += [flag, str(int(cfg[key]))]
    # 生成/采样参数透传（显式配置才上 CLI——留空保持端点缺省，不抢先覆盖；
    # 旗标拼写对齐 evalscope perf 1.15 Arguments：query-template /
    # apply-chat-template / frequency-penalty / repetition-penalty 等）
    if cfg.get("temperature") is not None:
        args += ["--temperature", str(float(cfg["temperature"]))]
    if cfg.get("top_p") is not None:
        args += ["--top-p", str(float(cfg["top_p"]))]
    if cfg.get("top_k") is not None:
        args += ["--top-k", str(int(cfg["top_k"]))]
    if cfg.get("min_tokens") is not None:
        args += ["--min-tokens", str(int(cfg["min_tokens"]))]
    if cfg.get("frequency_penalty") is not None:
        args += ["--frequency-penalty", str(float(cfg["frequency_penalty"]))]
    if cfg.get("repetition_penalty") is not None:
        args += ["--repetition-penalty", str(float(cfg["repetition_penalty"]))]
    if cfg.get("seed") is not None:
        args += ["--seed", str(int(cfg["seed"]))]
    if cfg.get("stop"):
        stop = cfg["stop"] if isinstance(cfg["stop"], list) else [cfg["stop"]]
        args += ["--stop", *[str(s) for s in stop]]
    if cfg.get("query_template"):
        args += ["--query-template", str(cfg["query_template"])]
    if cfg.get("apply_chat_template") is not None:
        args.append("--apply-chat-template" if cfg["apply_chat_template"]
                    else "--no-apply-chat-template")
    if cfg.get("no_test_connection"):
        args.append("--no-test-connection")
    return args + probe_args


def _group_summaries(output_dir: Path) -> tuple[
        dict[str, list[tuple[dict, dict, Path, int]]], list[str]]:
    """glob 全部档位产出并按并发分组（兼容普通/摸高两种目录布局）。

    看门狗截断可能杀在写盘中途：损坏/截断的产出跳过（告警），收集其余轮次。
    强制停止杀树后 summary json 只在完整跑完的档位存在——有 ``benchmark_data.db``
    无 summary 的档位按库兜底合成（逐请求记录流式落库，已完成请求都在），
    返回值第二项为兜底合成的档位标签（截断标记用）。
    """
    summaries = sorted(output_dir.glob("**/benchmark_summary.json"))
    if not summaries and not list(output_dir.glob("**/benchmark_data.db")):
        raise RuntimeError(f"未找到压测产出（{output_dir}/**/benchmark_summary.json），"
                           "查看 logs/perf.log")
    grouped: dict[str, list[tuple[dict, dict, Path, int]]] = {}
    for summary_path in summaries:
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except ValueError:
            logger.warning("跳过损坏/截断的压测产出 %s", summary_path)
            continue
        pct_rows: dict[str, dict] = {}
        pct_path = summary_path.parent / "benchmark_percentile.json"
        if pct_path.is_file():
            try:
                rows = json.loads(pct_path.read_text(encoding="utf-8"))
            except ValueError:
                rows = []
            pct_rows = {r.get("Percentiles"): r for r in rows
                        if r.get("Percentiles") in
                        {row for _n, (row, _c, _s) in _PERCENTILE_METRICS.items()}}
        concurrency = int(data.get("Concurrency") or 0)
        # 开环 QPS 档（Concurrency=-1/INF）按 rate 值区分档位
        if concurrency < 0:
            label = f"perf:q{float(data.get('Request Rate (req/s)') or 0):g}"
        else:
            label = f"perf:c{concurrency}"
        grouped.setdefault(label, []).append((data, pct_rows, summary_path, concurrency))
    partial_labels: list[str] = []
    seen_dirs = {p.parent for p in summaries}
    for db_path in sorted(output_dir.glob("**/benchmark_data.db")):
        if db_path.parent in seen_dirs:
            continue
        synthesized = _db_level_summary(db_path)
        if synthesized is None:
            continue
        label, concurrency = _dir_level_label(db_path.parent)
        logger.warning("档位 %s 无 benchmark_summary.json（强制停止截断），"
                       "按 benchmark_data.db 兜底合成（在途未返回请求不计入）", label)
        grouped.setdefault(label, []).append((synthesized, {}, db_path, concurrency))
        if label not in partial_labels:
            partial_labels.append(label)
    if not grouped:
        raise RuntimeError(f"压测产出为空（无已完成请求的档位，{output_dir}），"
                           "查看 logs/perf.log")
    return grouped, partial_labels


def _dir_level_label(level_dir: Path) -> tuple[str, int]:
    """从档位目录名（parallel_N_number_M / sla_parallel_N_run_i / rate_R_number_M）
    推断档位标签与并发（summary json 缺失时的兜底）。"""
    name = level_dir.name
    m = re.search(r"(?:sla_)?parallel_(\d+)", name)
    if m:
        users = int(m.group(1))
        return f"perf:c{users}", users
    m = re.search(r"rate_([\d.]+)", name)
    if m:
        rate = float(m.group(1))
        return f"perf:q{rate:g}", -1
    return "perf:c0", 0


def _db_level_summary(db_path: Path) -> dict | None:
    """逐请求库 → evalscope benchmark_summary 同键位合成（强制停止兜底）。

    未返回的请求不会写入库（完成一条写一条），天然不计入统计。列缺失
    （上游版本差异）→ None 宁缺毋错；0 行（该档无任何请求完成）→ None。
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "SELECT latency, first_chunk_latency, time_per_output_token,"
            " inter_token_latencies, prompt_tokens, completion_tokens, success,"
            " start_time, completed_time FROM result").fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not rows:
        return None
    total = len(rows)
    ok = [r for r in rows if r[6]]
    n_ok = len(ok)
    lat_vals = [float(r[0]) for r in ok if r[0] is not None]
    ttft_vals = [float(r[1]) for r in ok if r[1] is not None]
    tpot_vals = [float(r[2]) for r in ok if r[2] is not None]
    itl_vals: list[float] = []
    for r in ok:
        if r[3]:
            with contextlib.suppress(ValueError):
                itl_vals.extend(float(iv) for iv in json.loads(r[3]))
    prompt_sum = sum(int(r[4] or 0) for r in ok)
    completion_sum = sum(int(r[5] or 0) for r in ok)
    starts = [float(r[7]) for r in ok if r[7] is not None]
    ends = [float(r[8]) for r in rows if r[8] is not None]
    span = (max(ends) - min(starts)) if starts and ends else 0.0

    def avg(vals: list[float], scale: float = 1.0) -> float | None:
        return (sum(vals) / len(vals)) * scale if vals else None

    _label, concurrency = _dir_level_label(db_path.parent)
    summary: dict = {
        "_partial": True,
        "Total Requests": total,
        "Failed Requests": total - n_ok,
        "Avg Latency (s)": avg(lat_vals),
        "TTFT (ms)": avg(ttft_vals, 1000.0),
        "TPOT (ms)": _gated(avg(tpot_vals, 1000.0)),
        "ITL (ms)": _gated(avg(itl_vals, 1000.0)),
        "Avg Input Tokens": (prompt_sum / n_ok) if n_ok else None,
        "Avg Output Tokens": (completion_sum / n_ok) if n_ok else None,
    }
    if span > 0:
        summary["Req Throughput (req/s)"] = n_ok / span
        summary["Output Throughput (tok/s)"] = completion_sum / span
    if concurrency >= 0:
        summary["Concurrency"] = concurrency
    else:
        m = re.search(r"rate_([\d.]+)", db_path.parent.name)
        summary["Request Rate (req/s)"] = float(m.group(1)) if m else 0.0
    return summary


def _resolve_bucket(cfg: dict) -> float:
    """时序桶间隔（秒）：perf.bucket_seconds 可覆盖，缺省 5s。"""
    bucket = cfg.get("bucket_seconds")
    if bucket is not None:
        bucket = float(bucket)
        if bucket <= 0:
            raise ValueError("perf.bucket_seconds 须为正数（秒）")
        return bucket
    return DEFAULT_BUCKET_SECONDS


def _level_metrics(items: list[tuple[dict, dict, Path, int]]) -> tuple[dict, int]:
    """同并发多 run 聚合 → (指标名→值, 总请求数)。

    均值/分位档跨 run 取均值；max 档从逐请求 db 取并跨 run 取最大（均值会
    低估尾部）。db 无缓存明细列 → cache_hit_rate 不产出（locust/自研引擎可测）。
    """
    def mean(display_key: str, source: str = "summary") -> float | None:
        vals = []
        for data, pct_rows, _path, _users in items:
            v = (data.get(display_key) if source == "summary"
                 else pct_rows.get(source, {}).get(display_key))
            if v is not None:
                vals.append(float(v))
        return sum(vals) / len(vals) if vals else None

    metrics: dict[str, float | None] = {
        name: mean(key) for name, key in _SUMMARY_METRICS.items()
    }
    avg_latency = mean("Avg Latency (s)")
    metrics["latency_avg_ms"] = avg_latency * 1000 if avg_latency is not None else None
    for name, (row, key, scale) in _PERCENTILE_METRICS.items():
        v = mean(key, source=row)
        metrics[name] = v * scale if v is not None else None
    # 最大值：分位文件无 max 档，逐请求 db 取（多 run 取最大；无 db 跳过）
    for _data, _pct, summary_path, _users in items:
        db_max = _db_level_max(summary_path.parent / "benchmark_data.db")
        if not db_max:
            continue
        for base, value in db_max.items():
            key = f"{base}_max_ms"
            metrics[key] = (value if metrics.get(key) is None
                            else max(metrics[key], value))
    total = sum(int(d.get("Total Requests") or 0)
                for d, _p, _path, _users in items)
    failed = sum(int(d.get("Failed Requests") or 0)
                 for d, _p, _path, _users in items)
    if total:
        metrics["error_rate"] = failed / total
    # ITL/TPOT 失真门槛（含全部档位与 max）：高并发读缓冲批量到达时 ≈0
    # （宁缺毋错）
    for name in list(metrics):
        if (name.startswith(("tpot", "itl")) and metrics.get(name) is not None
                and metrics[name] < _TOKEN_TIMING_FLOOR_MS):
            metrics[name] = None
    return metrics, total


def _resolve_force_stop_seconds(cfg: dict) -> float | None:
    """强制停止时长（perf.force_stop_minutes，分钟）→ 秒；None = 不设。

    到时监督循环直接杀树：在途请求即弃、未返回请求不计入统计（db 只落
    已完成请求），任务按截断收集收口。"""
    value = cfg.get("force_stop_minutes")
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError("perf.force_stop_minutes 须为 >0 的数字（分钟）")
    return float(value) * 60.0


@register_tool("evalscope-perf")
class PerfAdapter(ToolAdapter):
    name = "evalscope-perf"

    def build_config(self, req: ToolRunRequest) -> dict:
        """perf yaml 段 → evalscope perf CLI 参数（纯函数可单测）。

        负载/模式/时长解析共用 perf_common；本方法只做编排与组装。
        """
        cfg = req.perf
        if cfg.get("arrival"):
            raise ValueError("perf.arrival 到达模型（poisson/spike/custom）为 locust"
                             " 专有——evalscope perf 上游仅支持固定速率（perf.rate）")
        model = str(req.target.get("model") or "").strip()
        if not model:
            raise ValueError("evalscope-perf 需要 target.model")
        url, api_key = api_endpoint(req.target)
        dataset, prompt, synthetic = resolve_load(cfg)
        # max_tokens 优先级：任务显式 > 平台数据集 meta.output_tokens 定长兜底
        # （生成长度对齐场景设计）；其余采样参数只有任务侧一个来源，无歧义
        if dataset and cfg.get("max_tokens") is None:
            expect = dataset_output_tokens(dataset)
            if expect is not None:
                cfg = {**cfg, "max_tokens": expect}   # 拷贝，不改动原 spec dict
                logger.info("perf.max_tokens 未指定，按数据集 meta 定长兜底: %s",
                            expect)
        rates = _resolve_rate(cfg)   # QPS 模式（开环）：与 parallel/mode 互斥
        open_loop = rates is not None
        mode, parallel, probe_cfg = resolve_mode_parallel(cfg, allow_probe=True)
        if open_loop:
            parallel = rates   # rate 列表即梯度（档数载体：number 对齐/时长均分）
        plan = resolve_durations(cfg, mode=mode, n_levels=len(parallel))
        number = _resolve_number(cfg, plan, len(parallel), open_loop=open_loop)
        args = _assemble_args(
            cfg, url=url, model=model, api_key=api_key, parallel=parallel,
            number=number, plan=plan, dataset=dataset, prompt=prompt,
            synthetic=synthetic, output_dir=req.work_dir / "perf_raw",
            probe_args=_probe_args(mode, probe_cfg), rates=rates)
        # 看门狗即总时长预算：覆盖 extra.timeout（用户显式给的整场预算优先于兜底超时）
        timeout = (int(plan.watchdog) if plan.watchdog is not None
                   else int(req.extra.get("timeout", DEFAULT_TIMEOUT)))
        return {
            "args": args,
            "env": {"PYTHONIOENCODING": "utf-8"},
            "timeout": timeout,
            "duration_watchdog": int(plan.watchdog) if plan.watchdog is not None else None,
            "force_stop_seconds": _resolve_force_stop_seconds(cfg),
            "output_dir": str(req.work_dir / "perf_raw"),
            "dataset": dataset,   # run 阶段导出 prompt 文件用
            "synthetic": synthetic,   # run 阶段生成 prompt 文件用
            "open_loop": open_loop,   # collect 档位标签用（perf:q<rate>）
        }

    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        cfg = self.build_config(req)
        if cfg["dataset"]:   # 负载导出：平台数据集文件 → 逐行 prompt（custom 数据集）
            n = export_prompts(cfg["dataset"], req.work_dir / "load" / "prompts.txt")
            logger.info("perf 数据集负载导出 %d 条 prompt", n)
        elif cfg["synthetic"]:   # 合成负载：确定性生成（输入一致 → 曲线可比）
            prompts = generate_synthetic_prompts(**cfg["synthetic"])
            path = req.work_dir / "load" / "prompts.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(prompts) + "\n", encoding="utf-8")
            logger.info("perf 合成负载生成 %d 条 prompt（%d 字符）",
                        len(prompts), cfg["synthetic"]["prompt_length"])
        outcome = await run_sync_in_thread(self._exec, cfg, req.control)
        stop_info = self._stop_info(outcome, cfg)
        return self.collect(req, stop_info=stop_info)

    @staticmethod
    def _stop_info(outcome: dict, cfg: dict) -> dict | None:
        """停止结局 → _stop 截断标记（None = 自然跑完/普通看门狗超时，无标记）。"""
        how = outcome.get("how")
        if how != _HOW_FORCE and not outcome.get("stop_requested"):
            return None
        info: dict = {
            "how": how,
            "stop_requested": bool(outcome.get("stop_requested")),
            "graceful": how == _HOW_EXIT and bool(outcome.get("stop_requested")),
        }
        if outcome.get("force_reason"):
            info["reason"] = outcome["force_reason"]
        if cfg.get("force_stop_seconds") is not None:
            info["force_stop_minutes"] = cfg["force_stop_seconds"] / 60.0
        return info

    @staticmethod
    def _launch_command(binary: str, stop_file: Path) -> tuple[list[str], bool]:
        """evalscope 启动命令；能定位同 venv 解释器时走协停 wrapper（立即停止
        依赖它），否则直接跑 evalscope（仅强制停止可用，告警显式降级）。"""
        wrapper = Path(__file__).with_name("perf_wrapper.py")
        py = Path(binary).with_name("python.exe" if sys.platform == "win32"
                                    else "python")
        if py.is_file():
            return [str(py), str(wrapper), str(stop_file)], True
        logger.warning("未找到 evalscope 同目录解释器（%s），协停 wrapper 不可用——"
                       "仅强制停止可用，立即停止将退化为排空预算后杀树", py)
        return [binary], False

    def _exec(self, cfg: dict, control: ToolRunControl | None = None) -> dict:
        """Popen + 监督循环执行 evalscope perf（经协停 wrapper）；日志流式落盘。

        返回 {how, stop_requested, force_reason?}：exit=自然退出（含 wrapper
        软退出）；timeout=整场预算耗尽（看门狗/产物兜底语义不变）；force=
        强制杀树（在途即弃，已完成请求经 db 兜底收集）。"""
        binary = which_or_fail("evalscope",
                               "未找到 evalscope CLI——压测经其子进程执行，"
                               "请 pip install evalscope")
        output_dir = Path(cfg["output_dir"])
        log_path = output_dir.parent / "logs" / "perf.log"
        stop_file = output_dir.parent / "perf_control" / STOP_FILE_NAME
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.unlink(missing_ok=True)   # 重跑复用 work_dir 时清掉上轮残留
        launch, wrapped = self._launch_command(binary, stop_file)
        env = dict(os.environ) | {"PYTHONIOENCODING": "utf-8"} | cfg.get("env", {})
        log_path.parent.mkdir(parents=True, exist_ok=True)
        outcome: dict = {"how": _HOW_EXIT, "stop_requested": False}
        with open(log_path, "w", encoding="utf-8", errors="replace") as log_fh:
            proc = subprocess.Popen([*launch, *cfg["args"]], stdout=log_fh,
                                    stderr=subprocess.STDOUT, env=env)
            how = self._supervise(proc, cfg, control, stop_file, log_path,
                                  wrapped, outcome)
            outcome["how"] = how
        self._settle_outcome(outcome, cfg, log_path, proc)
        return outcome

    def _supervise(self, proc: subprocess.Popen, cfg: dict,
                   control: ToolRunControl | None, stop_file: Path,
                   log_path: Path, wrapped: bool, outcome: dict) -> str:
        """监督循环：0.5s 轮询退出码 + 停止事件 + 双截止（强制时长/整场预算）。

        立即停止：cancel_event 置位 → 写协停请求文件（wrapper 触发策略软退
        出：停止派发、排空在途）；排空超 GRACEFUL_DRAIN_BUDGET_S → 升级杀树。
        强制停止：force_event 置位 / force_stop_seconds 到时 → 立即杀树。"""
        start = time.monotonic()
        force_at = (start + cfg["force_stop_seconds"]
                    if cfg.get("force_stop_seconds") is not None else None)
        overall_at = start + cfg["timeout"]
        drain_at: float | None = None
        while True:
            rc = proc.poll()
            if rc is not None:
                return _HOW_EXIT
            now = time.monotonic()
            if control is not None and control.force_event is not None \
                    and control.force_event.is_set():
                return self._kill(proc, log_path, outcome, _HOW_FORCE,
                                  "强制停止（在途请求已取消，未返回请求不计入统计）")
            if force_at is not None and now >= force_at:
                return self._kill(proc, log_path, outcome, _HOW_FORCE,
                                  f"强制停止时长到（{cfg['force_stop_seconds'] / 60.0:g} 分钟），"
                                  "在途请求已取消，未返回请求不计入统计")
            if control is not None and control.cancel_event is not None \
                    and control.cancel_event.is_set():
                outcome["stop_requested"] = True
                if not stop_file.exists():
                    self._write_stop_file(stop_file, log_path, wrapped)
                    drain_at = now + GRACEFUL_DRAIN_BUDGET_S
                elif drain_at is not None and now >= drain_at:
                    return self._kill(proc, log_path, outcome, _HOW_FORCE,
                                      f"排空在途超 {GRACEFUL_DRAIN_BUDGET_S:g}s 未返回"
                                      "（服务端挂死），升级强制停止")
            if now >= overall_at:
                kill_tree(proc)
                self._log_marker(log_path, f"SUBPROCESS TIMEOUT after {cfg['timeout']}s")
                return _HOW_TIMEOUT
            time.sleep(SUPERVISE_POLL_INTERVAL_S)

    @staticmethod
    def _write_stop_file(stop_file: Path, log_path: Path, wrapped: bool) -> None:
        """协停请求落盘 + 日志标记（wrapper 轮询读取后触发策略软退出）。"""
        try:
            stop_file.write_text(
                json.dumps({"mode": "graceful", "ts": time.time()}),
                encoding="utf-8")
        except OSError as exc:
            logger.error("协停请求文件写入失败：%s（排空预算后仍会杀树兜底）", exc)
        if not wrapped:
            logger.warning("协停 wrapper 未启用（无同目录解释器），立即停止无效果，"
                           "将在排空预算后强制杀树")
        PerfAdapter._log_marker(log_path, "GRACEFUL_STOP_REQUESTED "
                                          "(stop dispatching, drain in-flight)")

    @staticmethod
    def _log_marker(log_path: Path, text: str) -> None:
        """perf.log 追加停止标记行（流式子进程输出之外的过程留痕）。"""
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n[aite-perf] {time.strftime('%H:%M:%S')} {text}\n")
        except OSError:
            pass

    def _kill(self, proc: subprocess.Popen, log_path: Path, outcome: dict,
              how: str, reason: str) -> str:
        """杀进程树 + 日志标记（Windows proc.kill 只杀直接子进程，孙进程残留）。"""
        kill_tree(proc)
        outcome["force_reason"] = reason
        logger.warning("perf 子进程强制终止：%s", reason)
        self._log_marker(log_path, f"FORCE_STOP_KILLED — {reason}")
        return how

    def _settle_outcome(self, outcome: dict, cfg: dict, log_path: Path,
                        proc: subprocess.Popen) -> None:
        """结局裁定（日志已流式落盘）：force=截断收集不报错；timeout 维持原
        看门狗/产物兜底语义；exit 非零 = 真失败抛错（错误提示从日志提取）。"""
        how = outcome["how"]
        if how == _HOW_FORCE:
            return
        out = self._read_log_tail(log_path)
        if how == _HOW_TIMEOUT:
            if cfg.get("duration_watchdog"):
                logger.warning("perf 总时长预算 %ss 耗尽，截断 probe 搜索"
                               "（已完成轮次保留）", cfg["duration_watchdog"])
                self._log_marker(log_path, f"DURATION_BUDGET_EXHAUSTED after "
                                           f"{cfg['duration_watchdog']}s")
                return
            summaries = list(Path(cfg["output_dir"])
                             .glob("**/benchmark_summary.json"))
            if summaries:
                logger.warning("压测子进程超时（%ss）但测量产物已完整（%s）"
                               "——按完成处理继续采集，结果可信度请核对"
                               " error_rate 与样本跨度", cfg["timeout"],
                               summaries[0].parent)
                return
            raise RuntimeError(f"压测超时（{cfg['timeout']}s），日志: {log_path}")
        if proc.returncode != 0:
            raise RuntimeError(f"evalscope perf 执行失败（exit={proc.returncode}），"
                               f"{self._api_error_hint(out)}"
                               f"日志: {log_path}\n{out[-2000:]}")

    @staticmethod
    def _read_log_tail(log_path: Path, limit: int = 20000) -> str:
        try:
            return log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    @staticmethod
    def _api_error_hint(out: str) -> str:
        """提取服务端拒绝类错误（401 密钥/402 余额/403 权限）到异常消息——
        原则 9 错误显式化：根因直达，不必翻日志。"""
        hits = re.findall(r"Non-retryable error \(HTTP (\d+)\): (.+)", out)
        if not hits:
            return ""
        code, msg = hits[-1]
        msg = re.sub(r"\s*Please check your --url and --api settings\.?$", "",
                     msg.strip())
        return f"服务端拒绝（HTTP {code}）——{msg[:200]}。"

    def collect(self, req: ToolRunRequest,
                stop_info: dict | None = None) -> ToolRunResult:
        """解析各档 benchmark_summary/percentile → 指标行（dataset=perf:c<并发>）。

        统一 glob ``**/benchmark_summary.json``：普通模式 ``parallel_X_number_Y/``
        与摸高模式 ``sla_tuning/sla_parallel_N_run_i/`` 两种布局都覆盖；
        同并发多 run（probe 的 runs>1）按均值聚合、count 求和。强制停止截断的
        档位（有 db 无 summary）按库兜底合成，stop_info 经 _stop 标记随结果
        与 timeseries 落盘（前端展示截断口径）。"""
        cfg = self.build_config(req)   # 纯函数，重复调用拿 output_dir
        out = ToolRunResult()
        grouped, partial_labels = _group_summaries(Path(cfg["output_dir"]))
        bucket = _resolve_bucket(cfg)
        timeseries: dict = {"engine": self.name, "run_name": req.run_name,
                            "bucket_seconds": bucket,
                            "series": [], "combined": [], "errors": []}

        def _label_key(item):
            label = item[0]
            return (0, int(label.rsplit("c", 1)[1])) if ":c" in label                 else (1, float(label.rsplit("q", 1)[1]))

        # 各档 db 共用 perf 进程时钟（同源相对秒）——换算 epoch 基准后点即可
        # 映射墙钟；combined 用同源原值直接拼接（天然连续，无近似偏移）
        level_points: list[tuple[str, int, list[dict], float]] = []
        min_raw, max_raw = None, 0.0
        for label, items in sorted(grouped.items(), key=_label_key):
            for _d, _p, summary_path, users in items:
                agg = _aggregate_level_db(
                    summary_path.parent / "benchmark_data.db", bucket)
                if agg:
                    points, raw_t0 = agg
                    level_points.append((label, users, points, raw_t0))
                    min_raw = raw_t0 if min_raw is None else min(min_raw, raw_t0)
                    max_raw = max(max_raw, raw_t0 + points[-1]["t"] + bucket)
        epoch_base = (time.time() - max_raw - 1.0) if max_raw else 0.0

        for label, items in sorted(grouped.items(), key=_label_key):
            metrics, total = _level_metrics(items)
            append_metric_rows(out.rows, req.metric_thresholds, label,
                               metrics, total)
            tps = metrics.get("output_tps")
            if tps is not None:
                out.raw_score[label] = float(tps)
        for label, users, points, raw_t0 in level_points:
            for pt in points:
                pt["ts"] = round(epoch_base + raw_t0 + pt["t"], 3)
            timeseries["series"].append({
                "users": users, "run": 0, "label": label, "points": points})
            for pt in points:   # combined：同源原值直接拼接（天然阶梯连续）
                timeseries["combined"].append({
                    **pt, "t": round(raw_t0 + pt["t"] - (min_raw or 0), 3),
                    "users": users})

        total_failed = sum(int(d.get("Failed Requests") or 0)
                           for items in grouped.values()
                           for d, _p, _path, _users in items)
        if total_failed:
            # evalscope 错误文本在 pickle 响应里，只给计数（文本级错误分布见 locust）
            timeseries["errors"] = [{"key": "failed", "count": total_failed}]
        if stop_info or partial_labels:
            timeseries["stop"] = {**(stop_info or {}),
                                  "truncated_levels": partial_labels}
            # 细节随 run details 持久化（persist_tool_result 落 result.perf），
            # 前端详情页据 _stop 展示截断口径（在途未返回请求不计入统计）
            out.perf["_stop"] = timeseries["stop"]
        from aite.tools import serialize_summaries  # 懒导入：包 __init__ 注册适配器，顶层导入成环
        timeseries["summaries"] = serialize_summaries(out.rows)
        write_timeseries(out, Path(cfg["output_dir"]).parent, timeseries)
        out.report_files.update(glob_report_files(
            Path(cfg["output_dir"]).parent, (
                "perf_raw/**/performance_summary.txt",
                "perf_raw/**/perf_report.html",
                "perf_raw/**/sla_summary.json",
                "perf_raw/**/workload_timeline.json",
                "perf_raw/**/benchmark_data.db",
                "logs/perf.log",
            )))
        return out

    def list_tasks(self) -> list[dict]:
        """压测无任务目录概念，返回单一 chat 压测伪任务（指标清单）。"""
        return [{
            "name": "chat",
            "pretty_name": "Chat 压测（OpenAI 兼容端点）",
            "metrics": ["ttft_ms", "ttft_p50_ms", "ttft_p90_ms", "ttft_p95_ms",
                        "ttft_p99_ms", "ttft_max_ms",
                        "latency_avg_ms", "latency_p50_ms", "latency_p90_ms",
                        "latency_p95_ms", "latency_p99_ms", "latency_max_ms",
                        "tpot_ms", "tpot_p50_ms", "tpot_p90_ms", "tpot_p95_ms",
                        "tpot_p99_ms", "tpot_max_ms",
                        "itl_ms", "itl_p50_ms", "itl_p90_ms", "itl_p95_ms",
                        "itl_p99_ms", "itl_max_ms",
                        "qps", "output_tps",
                        "avg_input_tokens", "avg_output_tokens", "error_rate"],
            "tags": ["perf"],
            "few_shot_num": None,
        }]
