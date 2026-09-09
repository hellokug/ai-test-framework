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
- ``max_tokens``：标量 = 定长；``[min, max]`` = 每请求均匀采样（输出长度分布）
- ``min/max_prompt_length`` + ``tokenizer_path``：按真实 token 数过滤输入

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

collect 从各档 ``benchmark_summary.json`` / ``benchmark_percentile.json`` 提取
指标 → 每档每指标一行 ``dataset="perf:c<并发>"``；阈值方向区分越高/越低越好。
locust 适配器后置备选（非 LLM 端点 / 极端规模分布式场景）。
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from aite.tools.base import (
    ToolAdapter,
    ToolMetricRow,
    ToolRunRequest,
    ToolRunResult,
    api_endpoint,
    register_tool,
    run_sync_in_thread,
)
from aite.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 3600     # 整场压测总超时（秒），req.extra.timeout 可覆盖
_DURATION_DRIVEN_NUMBER_CAP = 1_000_000   # 时长驱动安全上限（先到为准，防默认条数提前结束）
DEFAULT_BUCKET_SECONDS = 5.0   # 时序聚合桶间隔（秒），perf.bucket_seconds 可覆盖

_MODES = {"baseline", "ramp", "probe", "soak"}
# evalscope SLA 可断言指标（sla_run.get_metric_values 键）；
# 单位：latency 秒、ttft/tpot 毫秒、rps/tps 原生单位
_SLA_METRICS = {"avg_latency", "avg_ttft", "avg_tpot", "p99_latency", "p99_ttft",
                "p99_tpot", "p50_ttft", "p90_ttft", "p50_tpot", "p90_tpot",
                "rps", "tps"}
# 平台数据集输入键约定（与 aite/datasets/local 一致，此处自包含实现避免深 import）
_INPUT_KEYS = ("input", "question", "instruction", "prompt", "query", "user")

# benchmark_summary.json → 入库指标映射（None 跳过）
_SUMMARY_METRICS = {
    "ttft_ms": "TTFT (ms)",
    "tpot_ms": "TPOT (ms)",
    "itl_ms": "ITL (ms)",
    "qps": "Req Throughput (req/s)",
    "output_tps": "Output Throughput (tok/s)",
}
_PERCENTILE_METRICS = {   # benchmark_percentile.json（取 95% 行，秒→毫秒）
    "ttft_p95_ms": ("TTFT (ms)", 1.0),
    "latency_p95_ms": ("Latency (s)", 1000.0),
}
# 越低越好（阈值方向 <=）：延迟/首 token/错误率；qps/output_tps 越高越好
_LOWER_IS_BETTER = {"ttft_ms", "ttft_p95_ms", "tpot_ms", "itl_ms",
                    "latency_avg_ms", "latency_p95_ms", "error_rate"}


def _positive_ints(value) -> bool:
    return (isinstance(value, list) and value
            and all(isinstance(v, int) and not isinstance(v, bool) and v >= 1
                    for v in value))


def _expand_ramp(ramp: dict) -> list[int]:
    """from/to/step → 并发梯度（to 必含：爬坡终点即最大并发）。"""
    try:
        start, stop, step = int(ramp["from"]), int(ramp["to"]), int(ramp["step"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("perf.ramp 需要 from / to / step 整数") from exc
    if start < 1 or stop < start or step < 1:
        raise ValueError("perf.ramp 需要 1 ≤ from ≤ to 且 step ≥ 1")
    levels = list(range(start, stop + 1, step))
    if levels[-1] != stop:
        levels.append(stop)
    return levels


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


def _export_prompts(dataset: str, dest: Path) -> int:
    """平台数据集文件（jsonl/csv）→ 逐行 prompt 文件（evalscope custom 数据集）。

    返回导出条数。行数 = 压测条数；输入键取 input/question/instruction/
    prompt/query/user 首个命中（与平台 local 数据集约定一致）。
    """
    src = Path(dataset)
    if not src.is_file():
        raise ValueError(f"perf.dataset 文件不存在: {src}")
    records: list[dict] = []
    with open(src, encoding="utf-8") as fh:
        if src.suffix.lower() == ".csv":
            records = [dict(row) for row in csv.DictReader(fh)]
        else:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"{src.name}:{lineno}: 行不是合法 JSON: {exc}") from exc
                if not isinstance(rec, dict):
                    raise ValueError(f"{src.name}:{lineno}: 行须为 JSON 对象")
                records.append(rec)
    prompts: list[str] = []
    for i, rec in enumerate(records, 1):
        for key in _INPUT_KEYS:
            value = rec.get(key)
            if isinstance(value, str) and value.strip():
                prompts.append(value.strip())
                break
        else:
            raise ValueError(f"{src.name}: 第 {i} 条记录无输入字段（{list(_INPUT_KEYS)}）")
    if not prompts:
        raise ValueError(f"{src.name}: 没有可用的 prompt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(prompts) + "\n", encoding="utf-8")
    return len(prompts)


_SYNTHETIC_SKELETON = (
    "请围绕以下主题撰写一段说明文字，要求内容连贯、信息量充足：{filler}。"
    "主题编号 {i}，请从多个角度展开论述，保持输出长度稳定。"
)


def generate_synthetic_prompts(count: int, prompt_length: int) -> list[str]:
    """确定性合成负载：固定句式骨架 + 填充文本到目标字符数（每次生成完全一致）。

    「框架自动生成固定输入输出」——无数据集时压测可复现（输入一致则输出分布
    一致，TTFT/吞吐曲线可比）。填充用循环数字串避免真实语义导致的缓存命中。
    """
    if count < 1 or prompt_length < 1:
        raise ValueError("perf.synthetic 的 count / prompt_length 须为 ≥1 整数")
    prompts = []
    for i in range(1, count + 1):
        filler_cycle = "0123456789"
        filler = (filler_cycle * (prompt_length // len(filler_cycle) + 1))[:prompt_length]
        prompts.append(_SYNTHETIC_SKELETON.format(i=i, filler=filler)[: prompt_length + 60])
    return prompts


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


def _aggregate_level_db(db_path: Path, bucket: float) -> list[dict] | None:
    """单档 benchmark_data.db → 桶级时序点（大屏曲线数据源）。

    逐请求记录（evalscope perf 子进程流式写入）事后聚合：按 completed_time 落桶，
    t = 桶起点相对该档首个请求开始的秒数。成功请求计 TTFT/延迟分位，失败计错误数；
    rps/tok_s = 桶内完成请求数 / completion_tokens 和 ÷ 桶时长。
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "SELECT completed_time, latency, first_chunk_latency, success,"
            " completion_tokens FROM result ORDER BY completed_time").fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not rows:
        return None
    t0 = rows[0][0]
    buckets: dict[int, dict] = {}
    for completed, latency, ttft, ok, tokens in rows:
        idx = int(max((completed or 0) - t0, 0.0) // bucket)
        b = buckets.setdefault(idx, {"n": 0, "errors": 0, "tokens": 0.0,
                                     "ttft": [], "latency": []})
        b["n"] += 1
        b["tokens"] += float(tokens or 0)
        if ok:
            if ttft is not None:
                b["ttft"].append(float(ttft) * 1000.0)   # 秒 → 毫秒
            if latency is not None:
                b["latency"].append(float(latency) * 1000.0)
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
            "rps": round(b["n"] / bucket, 3),
            "tok_s": round(b["tokens"] / bucket, 1),
            "errors": b["errors"],
        })
    return series


@register_tool("evalscope-perf")
class PerfAdapter(ToolAdapter):
    name = "evalscope-perf"

    def build_config(self, req: ToolRunRequest) -> dict:
        """perf yaml 段 → evalscope perf CLI 参数（纯函数可单测）。"""
        cfg = req.perf
        model = str(req.target.get("model") or "").strip()
        if not model:
            raise ValueError("evalscope-perf 需要 target.model")
        url, api_key = api_endpoint(req.target)

        # ---- 负载：dataset / prompt / synthetic 三选一 ----
        dataset = str(cfg.get("dataset") or "").strip()
        prompt = str(cfg.get("prompt") or "").strip()
        synthetic = cfg.get("synthetic")
        chosen = [k for k, v in (("dataset", dataset), ("prompt", prompt),
                                 ("synthetic", synthetic)) if v]
        if len(chosen) > 1:
            raise ValueError("perf.dataset / perf.prompt / perf.synthetic 三选一")
        if not chosen:
            raise ValueError("perf 需要 perf.dataset（数据集文件）/ perf.prompt（固定提示词）"
                             " / perf.synthetic（合成负载）三选一")
        if synthetic:
            if (not isinstance(synthetic, dict)
                    or not isinstance(synthetic.get("count"), int)
                    or isinstance(synthetic.get("count"), bool) or synthetic["count"] < 1
                    or not isinstance(synthetic.get("prompt_length"), int)
                    or isinstance(synthetic.get("prompt_length"), bool)
                    or synthetic["prompt_length"] < 1):
                raise ValueError("perf.synthetic 须为 {count: ≥1, prompt_length: ≥1}"
                                 "（条数 / 每条目标字符数）")
            synthetic = {"count": synthetic["count"],
                         "prompt_length": synthetic["prompt_length"]}
        output_dir = req.work_dir / "perf_raw"

        # ---- 模式 → 并发列表 / probe 参数 ----
        mode = cfg.get("mode")
        if mode is not None and mode not in _MODES:
            raise ValueError(f"perf.mode 须 ∈ {sorted(_MODES)}")
        parallel: list[int] | None = None
        probe_args: list[str] = []
        if mode == "baseline":
            b = cfg.get("baseline") or {}
            if not isinstance(b.get("parallel"), int) or isinstance(b.get("parallel"), bool):
                raise ValueError("perf.baseline 需要 parallel（整数）")
            parallel = [b["parallel"]]
        elif mode == "ramp":
            parallel = _expand_ramp(cfg.get("ramp") or {})
        elif mode == "soak":
            s = cfg.get("soak") or {}
            if not isinstance(s.get("parallel"), int) or isinstance(s.get("parallel"), bool):
                raise ValueError("perf.soak 需要 parallel（整数）")
            parallel = [s["parallel"]]
        elif mode == "probe":
            p = cfg.get("probe") or {}
            max_parallel = p.get("max_parallel")
            if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 1:
                raise ValueError("perf.probe 需要 max_parallel（≥1 整数，搜索上界）")
            start = p.get("from", 1)
            if not isinstance(start, int) or isinstance(start, bool) or start < 1:
                raise ValueError("perf.probe.from 须为 ≥1 整数（搜索起点）")
            parallel = [start]
            probe_args = [
                "--sla-auto-tune", "--sla-variable", "parallel",
                "--sla-upper-bound", str(max_parallel),
                "--sla-params", _sla_params_json(p.get("sla") or {}),
            ]
            if p.get("min_parallel"):
                probe_args += ["--sla-lower-bound", str(int(p["min_parallel"]))]
            if p.get("runs"):
                probe_args += ["--sla-num-runs", str(int(p["runs"]))]
        elif mode is None:
            parallel = cfg.get("parallel") or [1]   # v1 兼容：直配梯度
        if mode is not None and cfg.get("parallel"):
            raise ValueError(f"perf.mode={mode} 模式下不要直接配 perf.parallel")

        if not _positive_ints(parallel):
            raise ValueError("perf.parallel 须为 ≥1 的整数数组")

        # ---- 时长：duration（整场总时长）与 level_duration（每档秒数）二选一 ----
        total_duration = cfg.get("duration")
        level_duration = cfg.get("level_duration")
        if total_duration is not None and level_duration is not None:
            raise ValueError("perf.duration 与 perf.level_duration 二选一"
                             "（整场总时长 / 每档秒数）")
        if total_duration is not None:
            total_duration = float(total_duration)
            if total_duration <= 0:
                raise ValueError("perf.duration 须为正数（秒）")
        if level_duration is not None:
            level_duration = float(level_duration)
            if level_duration <= 0:
                raise ValueError("perf.level_duration 须为正数（秒）")
        # probe 轮数不可预知，总时长无法均分 → 看门狗（到点杀进程收已完成轮次）
        watchdog = mode == "probe" and total_duration is not None
        # --duration 值：level_duration 直传；duration 单档直传、多档均分
        per_level: float | None = None
        if level_duration is not None:
            per_level = level_duration
        elif total_duration is not None and not watchdog:
            per_level = total_duration if len(parallel) == 1 else total_duration / len(parallel)

        # ---- 采样：number（显式配 → 先到为准；时长驱动 → 自动放大） ----
        number = cfg.get("number")
        if number is None:
            # 时长驱动缺省放大安全上限（防默认 8 条被先到为准掐死）；否则缺省 8
            number = (_DURATION_DRIVEN_NUMBER_CAP
                      if (per_level is not None or watchdog) else 8)
        if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
            number = [number] * len(parallel)   # 标量自动对齐梯度长度
        if not _positive_ints(number) or len(number) != len(parallel):
            raise ValueError("perf.number 须为 ≥1 整数，或与 parallel 等长的数组"
                             "（evalscope 校验规则：两者等长）")

        # ---- 组装 CLI ----
        args = [
            "perf",
            "--url", url, "--model", model, "--api", "openai",
            "--api-key", api_key,
            "--parallel", *[str(p) for p in parallel],
            "--number", *[str(n) for n in number],
            "--no-timestamp",
            "--outputs-dir", str(output_dir),
            "--stream" if cfg.get("stream", True) else "--no-stream",
        ]
        if per_level is not None:
            # 整数秒发整数（CLI 可读性：30 而非 30.0）
            dur = int(per_level) if per_level == int(per_level) else per_level
            args += ["--duration", str(dur)]
        if dataset or synthetic:
            # dataset 由 run 阶段导出 / synthetic 由 run 阶段生成，统一走 custom 数据集
            args += ["--dataset", "custom",
                     "--dataset-path", str(req.work_dir / "load" / "prompts.txt")]
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
        if cfg.get("no_test_connection"):
            args.append("--no-test-connection")
        args += probe_args
        # 看门狗即总时长预算：覆盖 extra.timeout（用户显式给的整场预算优先于兜底超时）
        timeout = (int(total_duration) if watchdog
                   else int(req.extra.get("timeout", DEFAULT_TIMEOUT)))
        return {
            "args": args,
            "env": {"PYTHONIOENCODING": "utf-8"},
            "timeout": timeout,
            "duration_watchdog": int(total_duration) if watchdog else None,
            "output_dir": str(output_dir),
            "dataset": dataset,   # run 阶段导出 prompt 文件用
            "synthetic": synthetic,   # run 阶段生成 prompt 文件用
        }

    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        cfg = self.build_config(req)
        if cfg["dataset"]:   # 负载导出：平台数据集文件 → 逐行 prompt（custom 数据集）
            n = _export_prompts(cfg["dataset"], req.work_dir / "load" / "prompts.txt")
            logger.info("perf 数据集负载导出 %d 条 prompt", n)
        elif cfg["synthetic"]:   # 合成负载：确定性生成（输入一致 → 曲线可比）
            prompts = generate_synthetic_prompts(**cfg["synthetic"])
            path = req.work_dir / "load" / "prompts.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(prompts) + "\n", encoding="utf-8")
            logger.info("perf 合成负载生成 %d 条 prompt（%d 字符）",
                        len(prompts), cfg["synthetic"]["prompt_length"])
        await run_sync_in_thread(self._exec, cfg)
        return self.collect(req)

    def _exec(self, cfg: dict) -> None:
        """subprocess 执行 evalscope perf；stdout/stderr 落日志，非 0 抛错。"""
        binary = shutil.which("evalscope")
        if binary is None:
            raise RuntimeError(
                "未找到 evalscope CLI——压测经其子进程执行，请 pip install evalscope")
        log_path = Path(cfg["output_dir"]).parent / "logs" / "perf.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ | cfg["env"]   # 合并：Windows 缺 SystemRoot 会崩
        try:
            proc = subprocess.run([binary, *cfg["args"]], capture_output=True,
                                  env=env, timeout=cfg["timeout"])
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"").decode("utf-8", errors="replace")
            err = (exc.stderr or b"").decode("utf-8", errors="replace")
            if cfg.get("duration_watchdog"):
                # probe + duration 总时长预算：到点截断不报错，已完成轮次的
                # 产出文件在盘上，collect 正常收集（无轮次完成则 collect 报错）
                logger.warning("perf 总时长预算 %ss 耗尽，截断 probe 搜索"
                               "（已完成轮次保留）", cfg["duration_watchdog"])
                log_path.write_text(
                    f"DURATION_BUDGET_EXHAUSTED after {cfg['duration_watchdog']}s\n"
                    f"{out}{err}", encoding="utf-8")
                return
            log_path.write_text(f"TIMEOUT after {cfg['timeout']}s\n{exc}",
                                encoding="utf-8")
            raise RuntimeError(f"压测超时（{cfg['timeout']}s）") from exc
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        log_path.write_text(out + err, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"evalscope perf 执行失败（exit={proc.returncode}），"
                               f"日志: {log_path}\n{out[-2000:]}")

    def collect(self, req: ToolRunRequest) -> ToolRunResult:
        """解析各档 benchmark_summary/percentile → 指标行（dataset=perf:c<并发>）。

        统一 glob ``**/benchmark_summary.json``：普通模式的 ``parallel_X_number_Y/``
        与摸高模式的 ``sla_tuning/sla_parallel_N_run_i/`` 两种布局都覆盖；
        同并发多 run（probe 的 runs>1）按均值聚合、count 求和。
        """
        cfg = self.build_config(req)   # 纯函数，重复调用拿 output_dir
        out = ToolRunResult()
        summaries = sorted(Path(cfg["output_dir"]).glob("**/benchmark_summary.json"))
        if not summaries:
            raise RuntimeError(f"未找到压测产出（{cfg['output_dir']}/**/benchmark_summary.json），"
                               "查看 logs/perf.log")
        grouped: dict[int, list[tuple[dict, dict, Path]]] = {}   # 并发 → [(summary, p95行, 路径)]
        for summary_path in summaries:
            try:
                data = json.loads(summary_path.read_text(encoding="utf-8"))
            except ValueError:
                # 看门狗截断可能杀在写盘中途：跳过损坏产出，收集其余轮次
                logger.warning("跳过损坏/截断的压测产出 %s", summary_path)
                continue
            concurrency = int(data.get("Concurrency") or 0)
            p95: dict = {}
            pct_path = summary_path.parent / "benchmark_percentile.json"
            if pct_path.is_file():
                try:
                    rows = json.loads(pct_path.read_text(encoding="utf-8"))
                except ValueError:
                    rows = []
                p95 = next((r for r in rows if r.get("Percentiles") == "95%"), {})
            grouped.setdefault(concurrency, []).append((data, p95, summary_path))

        # ---- 时序聚合（大屏曲线数据源）：逐档 benchmark_data.db → 桶级点 ----
        bucket = cfg.get("bucket_seconds")
        if bucket is not None:
            bucket = float(bucket)
            if bucket <= 0:
                raise ValueError("perf.bucket_seconds 须为正数（秒）")
        else:
            bucket = DEFAULT_BUCKET_SECONDS
        timeseries: dict = {"engine": self.name, "bucket_seconds": bucket,
                            "series": [], "errors": []}

        for concurrency, items in sorted(grouped.items()):
            label = f"perf:c{concurrency}"

            def mean(display_key: str, source: str = "summary",
                     *, _items=items) -> float | None:
                vals = []
                for data, p95, _path in _items:
                    v = (data.get(display_key) if source == "summary"
                         else p95.get(display_key))
                    if v is not None:
                        vals.append(float(v))
                return sum(vals) / len(vals) if vals else None

            metrics: dict[str, float | None] = {
                name: mean(key) for name, key in _SUMMARY_METRICS.items()
            }
            avg_latency = mean("Avg Latency (s)")
            metrics["latency_avg_ms"] = avg_latency * 1000 if avg_latency is not None else None
            for name, (key, scale) in _PERCENTILE_METRICS.items():
                v = mean(key, source="p95")
                metrics[name] = v * scale if v is not None else None
            total = sum(int(d.get("Total Requests") or 0) for d, _p, _path in items)
            failed = sum(int(d.get("Failed Requests") or 0) for d, _p, _path in items)
            if total:
                metrics["error_rate"] = failed / total
            for name, value in metrics.items():
                if value is None:
                    continue
                thr = req.metric_thresholds.get(name)
                higher = name not in _LOWER_IS_BETTER
                out.rows.append(ToolMetricRow(
                    dataset=label, metric=name, value=float(value), count=total,
                    threshold=thr, higher_is_better=higher,
                    passed=((float(value) >= thr) if higher
                            else (float(value) <= thr)) if thr is not None else None,
                ))
            tps = metrics.get("output_tps")
            if tps is not None:
                out.raw_score[label] = float(tps)

            for run_idx, (_d, _p, summary_path) in enumerate(items):
                points = _aggregate_level_db(
                    summary_path.parent / "benchmark_data.db", bucket)
                if points:
                    timeseries["series"].append({
                        "users": concurrency, "run": run_idx, "label": label,
                        "points": points})

        total_failed = sum(int(d.get("Failed Requests") or 0)
                           for items in grouped.values() for d, _p, _path in items)
        if total_failed:
            # evalscope 错误文本在 pickle 响应里，只给计数（文本级错误分布见 locust）
            timeseries["errors"] = [{"key": "failed", "count": total_failed}]
        base = Path(cfg["output_dir"]).parent
        if timeseries["series"]:
            ts_path = base / "timeseries.json"
            ts_path.write_text(json.dumps(timeseries, ensure_ascii=False),
                               encoding="utf-8")
            out.report_files["timeseries.json"] = str(ts_path)
        for pattern in ("perf_raw/**/performance_summary.txt",
                        "perf_raw/**/perf_report.html",
                        "perf_raw/**/sla_summary.json",
                        "perf_raw/**/workload_timeline.json",
                        "perf_raw/**/benchmark_data.db",
                        "logs/perf.log"):
            for p in sorted(base.glob(pattern)):
                out.report_files[p.name] = str(p)
        return out

    def list_tasks(self) -> list[dict]:
        """压测无任务目录概念，返回单一 chat 压测伪任务（指标清单）。"""
        return [{
            "name": "chat",
            "pretty_name": "Chat 压测（OpenAI 兼容端点）",
            "metrics": ["ttft_ms", "ttft_p95_ms", "latency_avg_ms", "latency_p95_ms",
                        "tpot_ms", "itl_ms", "qps", "output_tps", "error_rate"],
            "tags": ["perf"],
            "few_shot_num": None,
        }]
