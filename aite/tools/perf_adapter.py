"""evalscope-perf 适配器（模块 E：性能测试执行引擎）。

suite yaml 写 ``engine: evalscope-perf`` 即路由到本适配器；压测委托给
evalscope perf 子命令（vLLM 社区同源的 LLM 压测方案：aiohttp + 多 worker，
TTFT/TPOT/ITL/吞吐原生分档），aite 负责编排与统一入库——不自研压测客户端
（单进程 asyncio 客户端在高并发下自身即瓶颈，测的是客户端排队不是服务端）。

**负载**（``perf.dataset`` 与 ``perf.prompt`` 二选一）：

- ``dataset``：本地 jsonl/csv 文件，行数 = 条数，输入键同平台数据集约定
  （input/question/instruction/prompt/query/user 取首个命中）；适配器导出为
  逐行 prompt 文件（evalscope ``custom`` 数据集），打流时逐条取用
- ``max_tokens``：标量 = 定长；``[min, max]`` = 每请求均匀采样（输出长度分布）
- ``min/max_prompt_length`` + ``tokenizer_path``：按真实 token 数过滤输入

**模式**（``perf.mode`` 四选一；省略 = 兼容直配 ``parallel``/``number``）：

- ``baseline``：固定并发基准 → 单值 ``--parallel``
- ``ramp``：爬坡（from/to/step 展开为并发梯度，``to`` 必含）→ 多值
  ``--parallel``；``duration`` = 单档运行秒数
- ``probe``：摸高（SLA 约束下二分搜索最大可持续并发）→ ``--sla-auto-tune``；
  ``sla: {指标: 上限}`` 全部按 ``<`` 组成 AND 组
- ``soak``：长稳（时间驱动）→ ``--parallel`` + ``--duration``；number 自动
  放大为安全上限（默认 1000 会让长稳提前结束）

采样：``warmup_num`` 预热不计入；``stream: true``（默认）才有真 TTFT；
``number``/``duration`` 先到为准。中途熔断上游不支持（SLA 档间判定），backlog。

collect 从各档 ``benchmark_summary.json`` / ``benchmark_percentile.json`` 提取
指标 → 每档每指标一行 ``dataset="perf:c<并发>"``；阈值方向区分越高/越低越好。
locust 适配器后置备选（非 LLM 端点 / 极端规模分布式场景）。
"""

from __future__ import annotations

import csv
import json
import os
import shutil
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
SOAK_NUMBER_CAP = 1_000_000   # 长稳模式安全上限（duration 驱动，number 先到即停）

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

        # ---- 负载：dataset 与 prompt 二选一 ----
        dataset = str(cfg.get("dataset") or "").strip()
        prompt = str(cfg.get("prompt") or "").strip()
        if dataset and prompt:
            raise ValueError("perf.dataset 与 perf.prompt 二选一")
        if not dataset and not prompt:
            raise ValueError("perf 需要 perf.dataset（数据集文件）或 perf.prompt（固定提示词）")
        output_dir = req.work_dir / "perf_raw"

        # ---- 模式 → 并发列表 / duration / probe 参数 ----
        mode = cfg.get("mode")
        if mode is not None and mode not in _MODES:
            raise ValueError(f"perf.mode 须 ∈ {sorted(_MODES)}")
        parallel: list[int] | None = None
        duration: float | None = None
        probe_args: list[str] = []
        soak = False
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
            soak = True
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

        # ---- 采样：number（先到为准）/ duration ----
        duration = float(cfg["duration"]) if cfg.get("duration") is not None else duration
        if duration is not None and duration <= 0:
            raise ValueError("perf.duration 须为正数（秒）")
        number = cfg.get("number")
        if number is None:
            # 长稳缺省放大安全上限（duration 驱动，避免默认值提前结束）；其余模式默认 8
            number = SOAK_NUMBER_CAP if soak else 8
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
        if duration is not None:
            # 整数秒发整数（CLI 可读性：30 而非 30.0）
            dur = int(duration) if duration == int(duration) else duration
            args += ["--duration", str(dur)]
        if dataset:
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
        return {
            "args": args,
            "env": {"PYTHONIOENCODING": "utf-8"},
            "timeout": int(req.extra.get("timeout", DEFAULT_TIMEOUT)),
            "output_dir": str(output_dir),
            "dataset": dataset,   # run 阶段导出 prompt 文件用
        }

    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        cfg = self.build_config(req)
        if cfg["dataset"]:   # 负载导出：平台数据集文件 → 逐行 prompt（custom 数据集）
            n = _export_prompts(cfg["dataset"], req.work_dir / "load" / "prompts.txt")
            logger.info("perf 数据集负载导出 %d 条 prompt", n)
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
        grouped: dict[int, list[tuple[dict, dict]]] = {}   # 并发 → [(summary, p95行)]
        for summary_path in summaries:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            concurrency = int(data.get("Concurrency") or 0)
            p95: dict = {}
            pct_path = summary_path.parent / "benchmark_percentile.json"
            if pct_path.is_file():
                rows = json.loads(pct_path.read_text(encoding="utf-8"))
                p95 = next((r for r in rows if r.get("Percentiles") == "95%"), {})
            grouped.setdefault(concurrency, []).append((data, p95))

        for concurrency, items in sorted(grouped.items()):
            label = f"perf:c{concurrency}"

            def mean(display_key: str, source: str = "summary",
                     *, _items=items) -> float | None:
                vals = []
                for data, p95 in _items:
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
            total = sum(int(d.get("Total Requests") or 0) for d, _ in items)
            failed = sum(int(d.get("Failed Requests") or 0) for d, _ in items)
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
        base = Path(cfg["output_dir"]).parent
        for pattern in ("perf_raw/**/performance_summary.txt",
                        "perf_raw/**/perf_report.html",
                        "perf_raw/**/sla_summary.json",
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
