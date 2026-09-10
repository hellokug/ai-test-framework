"""locust 适配器（通用 HTTP 压测，evalscope-perf 的并列备选）。

suite yaml 写 ``engine: locust`` 即路由到本适配器；压测委托 locust 子进程
（gevent 协程 + 分布式 worker 支持），aite 负责编排与统一入库。与
evalscope-perf 的分工：后者 LLM 原生指标（TPOT/ITL/吞吐分档）更全，本适配器
面向非 OpenAI 兼容端点改造 / 极端规模分布式 / 通用 HTTP 打点场景。

**复用 suite 的 ``perf`` 配置段**（与 evalscope-perf 同键同语义）：

- 负载：``dataset``（jsonl/csv 逐行 prompt）或 ``prompt`` 固定提示词，二选一
- 模式：``baseline`` / ``ramp`` / ``soak`` / 省略 mode 直配 ``parallel``；
  **probe 不支持**（SLA 二分搜索是 evalscope perf 专有，配置即报错）
- 时长：``duration``（整场总时长，多档均分）/ ``level_duration``（每档秒数）
  二选一——locust 每档是独立 ``-t`` 进程，到点自退出，无看门狗
- ``spawn_rate``：每档用户拉起速率（用户/秒），缺省 = 档位并发（瞬时拉满）
- ``number`` 对 locust 无语义（locust 按时长/用户数驱动，无请求数上限），
  配了忽略——同 ``limit_per_subset`` 对 pytest 无语义的先例

执行：每档一个 ``locust --headless -u N -r R -t S --csv`` 子进程，SSE 流式
逐块读首包计时 TTFT（自定义 ``TTFT`` 事件进 locust 统计），非 200 经
catch_response 记失败（错误码/异常文本进 _failures.csv）。

collect 解析各档 ``locust_uN_stats.csv`` / ``_failures.csv`` → 指标行
``dataset="locust:uN"``，指标名与 evalscope-perf 对齐（ttft_ms/latency/qps/
error_rate，趋势并列可比）；tpot/itl/output_tps locust 无来源，不产出。
"""

from __future__ import annotations

import csv
import json
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
from aite.tools.perf_common import (
    export_prompts,
    generate_synthetic_prompts,
    resolve_durations,
    resolve_load,
    resolve_mode_parallel,
)
from aite.tools.subproc import glob_report_files, run_tool_subprocess, which_or_fail
from aite.utils.logging import get_logger

logger = get_logger(__name__)

_LEVEL_GRACE = 120          # 每档子进程超时 = 档时长 + 宽限（locust -t 自退出）
# 越低越好（阈值方向 <=）；qps 越高越好（与 evalscope-perf 口径一致）
_LOWER_IS_BETTER = {"ttft_ms", "ttft_p95_ms", "latency_avg_ms", "latency_p95_ms",
                    "error_rate"}

def _num(row: dict, key: str) -> float | None:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def _history_series(output_dir: Path) -> list[dict]:
    """各档 _stats_history.csv → 时序 series（--csv-full-history，原生 1s 打点）。"""
    series = []
    for history_path in sorted(output_dir.glob("*_stats_history.csv")):
        users = history_path.name[len("locust_u"):-len("_stats_history.csv")]
        by_ts: dict[float, dict] = {}
        for hrow in csv.DictReader(history_path.open(encoding="utf-8")):
            rtype = hrow.get("Type")
            if rtype not in ("POST", "TTFT"):
                continue
            ts = _num(hrow, "Timestamp")
            if ts is None:
                continue
            b = by_ts.setdefault(ts, {"ttft": [], "latency": [], "rps": 0.0,
                                      "errors": 0.0})
            if rtype == "POST":
                b["rps"] += _num(hrow, "Requests/s") or 0.0
                b["errors"] += _num(hrow, "Failures/s") or 0.0
                for col in ("50%", "95%"):
                    if _num(hrow, col) is not None:
                        b["latency"].append(_num(hrow, col))
            else:
                for col in ("50%", "95%"):
                    if _num(hrow, col) is not None:
                        b["ttft"].append(_num(hrow, col))
        if not by_ts:
            continue
        t0 = min(by_ts)
        points = [_point(ts, by_ts[ts], t0) for ts in sorted(by_ts)]
        series.append({"users": int(users), "run": 0,
                       "label": f"locust:u{users}", "points": points})
    return series


def _point(ts: float, b: dict, t0: float) -> dict:
    """秒级时序点：POST 行的 rps/延迟分位 + TTFT 行分位（locust 无 token 口径）。"""
    return {
        "t": round(ts - t0, 3),
        "ttft_avg": round(sum(b["ttft"]) / len(b["ttft"]), 1) if b["ttft"] else None,
        "ttft_p95": round(max(b["ttft"]), 1) if b["ttft"] else None,
        "latency_avg": round(sum(b["latency"]) / len(b["latency"]), 1) if b["latency"] else None,
        "latency_p95": round(max(b["latency"]), 1) if b["latency"] else None,
        "rps": round(b["rps"], 3),
        "tok_s": None,   # locust 无 token 口径
        "errors": int(b["errors"]),
    }


def _errors_summary(failures: list[dict]) -> list[dict]:
    return [{"key": f["detail"].rsplit(" ×", 1)[0],
             "count": int(f["detail"].rsplit(" ×", 1)[1] or 0)}
            for f in failures]


_LOCUSTFILE_TEMPLATE = '''# 由 aite locust 适配器生成（勿手改；regenerate per run）
import json
import os
import time

from locust import HttpUser, constant, events, task

URL = os.environ["AITE_LOCUST_URL"]
MODEL = os.environ["AITE_LOCUST_MODEL"]
API_KEY = os.environ.get("AITE_LOCUST_API_KEY", "none")
STREAM = os.environ.get("AITE_LOCUST_STREAM", "1") == "1"
MAX_TOKENS = os.environ.get("AITE_LOCUST_MAX_TOKENS")
PROMPTS_FILE = os.environ["AITE_LOCUST_PROMPTS_FILE"]

with open(PROMPTS_FILE, encoding="utf-8") as fh:
    PROMPTS = [line for line in (l.strip() for l in fh) if line]


class ChatUser(HttpUser):
    """闭环用户：无间隔连发（= evalscope --parallel 语义）。"""

    wait_time = constant(0)
    host = os.environ.get("AITE_LOCUST_HOST")

    def on_start(self):
        self.client.headers.update({
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        })
        self._i = -1

    def _next_prompt(self):
        self._i = (self._i + 1) % len(PROMPTS)
        return PROMPTS[self._i]

    @task
    def chat(self):
        payload = {"model": MODEL, "stream": STREAM,
                   "messages": [{"role": "user", "content": self._next_prompt()}]}
        if MAX_TOKENS:
            payload["max_tokens"] = int(MAX_TOKENS)
        name = "POST /chat/completions"
        t0 = time.perf_counter()
        with self.client.post(URL, json=payload, name=name,
                              catch_response=True, timeout=300) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
                return
            if STREAM:
                ttft = None
                for line in resp.iter_lines():
                    if not line.startswith(b"data: "):
                        continue
                    if ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000.0
                    if line[6:].strip() == b"[DONE]":
                        break
                if ttft is not None:   # TTFT 独立事件：不混入延迟统计
                    events.request.fire(request_type="TTFT", name=name,
                                        response_time=ttft, response_length=0,
                                        exception=None, context={})
            resp.success()
'''


@register_tool("locust")
class LocustAdapter(ToolAdapter):
    name = "locust"

    def build_config(self, req: ToolRunRequest) -> dict:
        """perf yaml 段 → 每档 locust CLI 参数（纯函数可单测）。

        负载/模式/时长解析共用 perf_common（probe 拒绝、时长必配）。
        """
        cfg = req.perf
        model = str(req.target.get("model") or "").strip()
        if not model:
            raise ValueError("locust 需要 target.model")
        url, api_key = api_endpoint(req.target)
        dataset, prompt, synthetic = resolve_load(cfg)
        mode, parallel, _probe = resolve_mode_parallel(cfg, allow_probe=False)
        if cfg.get("number") is not None:
            logger.info("locust 忽略 perf.number（locust 按时长/用户数驱动）")
        plan = resolve_durations(cfg, mode=mode, n_levels=len(parallel), require=True)

        spawn_rate = cfg.get("spawn_rate")
        output_dir = req.work_dir / "locust_raw"
        runs = []
        for users, secs in zip(parallel, self._level_seconds(plan, parallel), strict=True):
            prefix = output_dir / f"locust_u{users}"
            rate = int(spawn_rate) if spawn_rate else users   # 缺省瞬时拉满
            runs.append({"users": users, "spawn_rate": rate, "seconds": float(secs),
                         "csv": str(prefix), "html": f"{prefix}_report.html"})

        return {
            "url": url, "model": model, "api_key": api_key,
            "dataset": dataset, "prompt": prompt, "synthetic": synthetic,
            "stream": bool(cfg.get("stream", True)),
            "max_tokens": cfg.get("max_tokens"),
            "locustfile": str(req.work_dir / "locustfile.py"),
            "prompts_file": str(req.work_dir / "load" / "prompts.txt"),
            "runs": runs,
            "timeout": int(req.extra.get("timeout", 3600)),
            "output_dir": str(output_dir),
        }

    @staticmethod
    def _level_seconds(plan, parallel: list[int]) -> list[float]:
        """每档秒数：level_duration 直传；duration 单档直传/多档均分。"""
        per = plan.per_level
        return [per] * len(parallel) if per is not None else [0.0] * len(parallel)

    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        cfg = self.build_config(req)
        self._write_load(cfg)
        await run_sync_in_thread(self._exec, cfg)
        return self.collect(req)

    def _write_load(self, cfg: dict) -> None:
        """负载落盘：locustfile + 逐行 prompt 文件（dataset/prompt/synthetic 统一）。"""
        Path(cfg["locustfile"]).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg["locustfile"]).write_text(_LOCUSTFILE_TEMPLATE, encoding="utf-8")
        dest = Path(cfg["prompts_file"])
        if cfg["dataset"]:
            n = export_prompts(cfg["dataset"], dest)
            logger.info("locust 数据集负载导出 %d 条 prompt", n)
        elif cfg["synthetic"]:
            prompts = generate_synthetic_prompts(**cfg["synthetic"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("\n".join(prompts) + "\n", encoding="utf-8")
            logger.info("locust 合成负载生成 %d 条 prompt", len(prompts))
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(cfg["prompt"] + "\n", encoding="utf-8")

    def _exec(self, cfg: dict) -> None:
        """逐档 subprocess 执行 locust；任一档超时/失败即抛错（后续档不跑）。"""
        binary = which_or_fail("locust", "未找到 locust CLI——请 pip install"
                               " 'aite[locust]'（或 pip install locust）")
        out_dir = Path(cfg["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir.parent / "logs" / "locust.log"
        env_extra = {
            "PYTHONUTF8": "1",
            "AITE_LOCUST_URL": cfg["url"],
            "AITE_LOCUST_MODEL": cfg["model"],
            "AITE_LOCUST_API_KEY": cfg["api_key"],
            "AITE_LOCUST_STREAM": "1" if cfg["stream"] else "0",
            "AITE_LOCUST_PROMPTS_FILE": cfg["prompts_file"],
        }
        if cfg["max_tokens"]:
            env_extra["AITE_LOCUST_MAX_TOKENS"] = str(int(cfg["max_tokens"]))
        host = cfg["url"].rsplit("/chat/completions", 1)[0]
        for i, run in enumerate(cfg["runs"]):
            args = [
                binary, "-f", cfg["locustfile"], "--headless", "--only-summary",
                "--csv", run["csv"], "--csv-full-history",   # 秒级时序（大屏曲线）
                "--html", run["html"],
                "-u", str(run["users"]), "-r", str(run["spawn_rate"]),
                "-t", f"{int(run['seconds'])}s", "--host", host,
            ]
            outcome, out, err = run_tool_subprocess(
                args, log_path=log_path, timeout=int(run["seconds"]) + _LEVEL_GRACE,
                env_extra=env_extra, append_log=i > 0,
                log_prefix=f"=== u{run['users']} × {run['seconds']}s ===\n")
            if outcome == "timeout":
                raise RuntimeError(
                    f"locust 档位超时（u{run['users']}，"
                    f"{int(run['seconds']) + _LEVEL_GRACE}s）")
            if outcome == "fail":
                raise RuntimeError(f"locust 执行失败（u{run['users']}），"
                                   f"日志: {log_path}\n{out[-2000:]}")

    def collect(self, req: ToolRunRequest) -> ToolRunResult:
        """解析各档 locust_uN_stats/_failures/history CSV → 指标行 + 时序 + 失败明细。"""
        cfg = self.build_config(req)   # 纯函数，重复调用拿 output_dir
        out = ToolRunResult()
        stats_files = sorted(Path(cfg["output_dir"]).glob("locust_u*_stats.csv"))
        if not stats_files:
            raise RuntimeError(f"未找到压测产出（{cfg['output_dir']}/locust_u*_stats.csv），"
                               "查看 logs/locust.log")
        for stats_path in stats_files:
            users = stats_path.name[len("locust_u"):-len("_stats.csv")]
            label = f"locust:u{users}"
            self._collect_level(req, out, stats_path, label)
            self._collect_level_failures(out, stats_path, label)

        timeseries = {"engine": self.name, "bucket_seconds": 1.0,
                      "series": _history_series(Path(cfg["output_dir"])),
                      "errors": _errors_summary(out.failures)}
        if timeseries["series"]:
            ts_path = Path(cfg["output_dir"]).parent / "timeseries.json"
            ts_path.write_text(json.dumps(timeseries, ensure_ascii=False),
                               encoding="utf-8")
            out.report_files["timeseries.json"] = str(ts_path)
        out.report_files.update(glob_report_files(Path(cfg["output_dir"]).parent, (
            "locust_raw/**/*.html",
            "locust_raw/**/*_stats.csv",
            "locust_raw/**/*_stats_history.csv",
            "logs/locust.log",
        )))
        return out

    def _collect_level(self, req: ToolRunRequest, out: ToolRunResult,
                       stats_path: Path, label: str) -> None:
        """单档 stats CSV → 指标行（口径：POST 行，Aggregated 被双事件污染）。"""
        rows = list(csv.DictReader(stats_path.open(encoding="utf-8")))
        agg = (next((r for r in rows if r.get("Type") == "POST"), None)
               or next((r for r in rows if r.get("Type") == "Aggregated"), None))
        if agg is None:
            logger.warning("locust 产出缺少 POST/Aggregated 行，跳过 %s", stats_path)
            return
        ttft = next((r for r in rows if r.get("Type") == "TTFT"), None)
        total = _num(agg, "Request Count") or 0
        failed = _num(agg, "Failure Count") or 0
        metrics = {
            "latency_avg_ms": _num(agg, "Average Response Time"),
            "latency_p95_ms": _num(agg, "95%"),
            "qps": _num(agg, "Requests/s"),
            "error_rate": (failed / total) if total else None,
        }
        if ttft is not None:   # 非流式无 TTFT 行 → 跳过（口径同 perf：None 不入库）
            metrics["ttft_ms"] = _num(ttft, "Average Response Time")
            metrics["ttft_p95_ms"] = _num(ttft, "95%")
        for name, value in metrics.items():
            if value is None:
                continue
            thr = req.metric_thresholds.get(name)
            higher = name not in _LOWER_IS_BETTER
            out.rows.append(ToolMetricRow(
                dataset=label, metric=name, value=float(value), count=int(total),
                threshold=thr, higher_is_better=higher,
                passed=((float(value) >= thr) if higher
                        else (float(value) <= thr)) if thr is not None else None,
            ))
        if metrics.get("qps") is not None:
            out.raw_score[label] = metrics["qps"]

    @staticmethod
    def _collect_level_failures(out: ToolRunResult, stats_path: Path, label: str) -> None:
        """单档 _failures.csv → 失败明细（错误文本 × 次数）。"""
        failures_path = stats_path.with_name(
            stats_path.name.replace("_stats.csv", "_failures.csv"))
        if failures_path.is_file():
            for frow in csv.DictReader(failures_path.open(encoding="utf-8")):
                out.failures.append({
                    "dataset": label, "case": frow.get("Method", ""),
                    "detail": f"{frow.get('Error', '')}"
                              f" ×{frow.get('Occurrences', '')}",
                })

    def list_tasks(self) -> list[dict]:
        """locust 无任务目录概念，返回单一 chat 压测伪任务（指标清单）。"""
        return [{
            "name": "chat",
            "pretty_name": "Chat 压测（通用 HTTP / locust）",
            "metrics": ["ttft_ms", "ttft_p95_ms", "latency_avg_ms", "latency_p95_ms",
                        "qps", "error_rate"],
            "tags": ["perf", "locust"],
            "few_shot_num": None,
        }]
