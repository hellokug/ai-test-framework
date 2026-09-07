"""evalscope-perf 适配器（模块 E：性能测试执行引擎）。

suite yaml 写 ``engine: evalscope-perf`` 即路由到本适配器；压测委托给
evalscope perf 子命令（vLLM 社区同源的 LLM 压测方案：aiohttp + 多 worker，
TTFT/TPOT/ITL/吞吐原生分档），aite 负责编排与统一入库——不自研压测客户端
（单进程 asyncio 客户端在高并发下自身即瓶颈，测的是客户端排队不是服务端）。

- 并发梯度：``perf.parallel: [1, 4, 16]`` 一次 run 全扫（evalscope 原生多值），
  每档独立结果目录 ``parallel_X_number_Y/``
- ``perf.number`` 标量自动复制对齐 parallel 长度（evalscope 校验规则：等长）
- 采样语义：``warmup_num`` 预热不计入；``stream: true``（默认）才有真 TTFT
- collect 从各档 ``benchmark_summary.json`` / ``benchmark_percentile.json``
  提取指标 → 每档每指标一行 ``dataset="perf:c<并发>"`` 入 run_metric_summary
- 阈值：显式 ``metric_thresholds``（如 ``{ttft_ms: 3000}``）可选，不配则只
  记录不判定（性能绝对阈值换环境即失效；基线回归对比后置 P1）
- locust 适配器后置备选（非 LLM 端点 / 极端规模分布式场景）
"""

from __future__ import annotations

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

DEFAULT_TIMEOUT = 3600   # 整场压测总超时（秒），req.extra.timeout 可覆盖

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


@register_tool("evalscope-perf")
class PerfAdapter(ToolAdapter):
    name = "evalscope-perf"

    def build_config(self, req: ToolRunRequest) -> dict:
        """perf yaml 段 → evalscope perf CLI 参数（纯函数可单测）。"""
        cfg = req.perf
        prompt = str(cfg.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("perf 需要 perf.prompt（固定提示词；数据集引用后置）")
        parallel = cfg.get("parallel") or [1]
        if not _positive_ints(parallel):
            raise ValueError("perf.parallel 须为 ≥1 的整数数组")
        number = cfg.get("number", 8)
        if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
            number = [number] * len(parallel)   # 标量自动对齐梯度长度
        if not _positive_ints(number) or len(number) != len(parallel):
            raise ValueError("perf.number 须为 ≥1 整数，或与 parallel 等长的数组"
                             "（evalscope 校验规则：两者等长）")
        model = str(req.target.get("model") or "").strip()
        if not model:
            raise ValueError("evalscope-perf 需要 target.model")
        url, api_key = api_endpoint(req.target)

        output_dir = req.work_dir / "perf_raw"
        args = [
            "perf",
            "--url", url, "--model", model, "--api", "openai",
            "--api-key", api_key,
            "--parallel", *[str(p) for p in parallel],
            "--number", *[str(n) for n in number],
            "--prompt", prompt,
            "--no-timestamp",
            "--outputs-dir", str(output_dir),
            "--stream" if cfg.get("stream", True) else "--no-stream",
        ]
        if cfg.get("max_tokens"):
            args += ["--max-tokens", str(int(cfg["max_tokens"]))]
        if cfg.get("warmup_num"):
            args += ["--warmup-num", str(int(cfg["warmup_num"]))]
        if cfg.get("no_test_connection"):
            args.append("--no-test-connection")
        return {
            "args": args,
            "env": {"PYTHONIOENCODING": "utf-8"},
            "timeout": int(req.extra.get("timeout", DEFAULT_TIMEOUT)),
            "output_dir": str(output_dir),
        }

    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        cfg = self.build_config(req)
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
        """解析各档 benchmark_summary/percentile → 指标行（dataset=perf:c<并发>）。"""
        cfg = self.build_config(req)   # 纯函数，重复调用拿 output_dir
        out = ToolRunResult()
        summaries = sorted(Path(cfg["output_dir"]).glob("*/parallel_*/benchmark_summary.json"))
        if not summaries:
            raise RuntimeError(f"未找到压测产出（{cfg['output_dir']}/*/parallel_*/"
                               "benchmark_summary.json），查看 logs/perf.log")
        for summary_path in summaries:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            concurrency = int(data.get("Concurrency") or 0)
            label = f"perf:c{concurrency}"
            count = int(data.get("Total Requests") or 0)
            metrics: dict[str, float | None] = {
                name: data.get(key) for name, key in _SUMMARY_METRICS.items()
            }
            avg_latency = data.get("Avg Latency (s)")
            metrics["latency_avg_ms"] = avg_latency * 1000 if avg_latency is not None else None
            pct_path = summary_path.parent / "benchmark_percentile.json"
            if pct_path.is_file():
                rows = json.loads(pct_path.read_text(encoding="utf-8"))
                p95 = next((r for r in rows if r.get("Percentiles") == "95%"), {})
                for name, (key, scale) in _PERCENTILE_METRICS.items():
                    v = p95.get(key)
                    metrics[name] = (v * scale) if v is not None else None
            failed = int(data.get("Failed Requests") or 0)
            if count:
                metrics["error_rate"] = failed / count
            for name, value in metrics.items():
                if value is None:
                    continue
                thr = req.metric_thresholds.get(name)
                higher = name not in _LOWER_IS_BETTER
                out.rows.append(ToolMetricRow(
                    dataset=label, metric=name, value=float(value), count=count,
                    threshold=thr, higher_is_better=higher,
                    passed=((float(value) >= thr) if higher
                            else (float(value) <= thr)) if thr is not None else None,
                ))
            tps = data.get("Output Throughput (tok/s)")
            if tps is not None:
                out.raw_score[label] = float(tps)
        base = Path(cfg["output_dir"]).parent
        for pattern in ("perf_raw/**/performance_summary.txt",
                        "perf_raw/**/perf_report.html",
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
