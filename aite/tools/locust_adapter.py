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
from aite.tools.perf_adapter import _export_prompts
from aite.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 3600      # 整场压测总超时（秒），req.extra.timeout 可覆盖
_LEVEL_GRACE = 120          # 每档子进程超时 = 档时长 + 宽限（locust -t 自退出）

_MODES = {"baseline", "ramp", "soak"}
# 越低越好（阈值方向 <=）；qps 越高越好（与 perf_adapter 口径一致）
_LOWER_IS_BETTER = {"ttft_ms", "ttft_p95_ms", "latency_avg_ms", "latency_p95_ms",
                    "error_rate"}

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


def _expand_ramp(ramp: dict) -> list[int]:
    """from/to/step → 并发梯度（与 perf_adapter 同规则：to 必含）。"""
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


def _resolve_levels(cfg: dict, parallel: list[int]) -> list[float]:
    """duration / level_duration → 每档秒数（与 perf_adapter 同语义）。"""
    total = cfg.get("duration")
    per = cfg.get("level_duration")
    if total is not None and per is not None:
        raise ValueError("perf.duration 与 perf.level_duration 二选一"
                         "（整场总时长 / 每档秒数）")
    if total is not None:
        total = float(total)
        if total <= 0:
            raise ValueError("perf.duration 须为正数（秒）")
        return [total] if len(parallel) == 1 else [total / len(parallel)] * len(parallel)
    if per is not None:
        per = float(per)
        if per <= 0:
            raise ValueError("perf.level_duration 须为正数（秒）")
        return [per] * len(parallel)
    raise ValueError("locust 压测需要时长驱动：配 perf.duration（整场总时长）或"
                     " perf.level_duration（每档秒数）——locust 无请求数上限语义")


@register_tool("locust")
class LocustAdapter(ToolAdapter):
    name = "locust"

    def build_config(self, req: ToolRunRequest) -> dict:
        """perf yaml 段 → 每档 locust CLI 参数（纯函数可单测）。"""
        cfg = req.perf
        model = str(req.target.get("model") or "").strip()
        if not model:
            raise ValueError("locust 需要 target.model")
        url, api_key = api_endpoint(req.target)

        dataset = str(cfg.get("dataset") or "").strip()
        prompt = str(cfg.get("prompt") or "").strip()
        if dataset and prompt:
            raise ValueError("perf.dataset 与 perf.prompt 二选一")
        if not dataset and not prompt:
            raise ValueError("locust 需要 perf.dataset（数据集文件）或 perf.prompt"
                             "（固定提示词）")

        mode = cfg.get("mode")
        if mode == "probe":
            raise ValueError("locust 不支持 probe 模式（SLA 二分搜索是 evalscope perf"
                             " 专有）——如需摸高请用 engine: evalscope-perf")
        if mode is None:
            parallel = cfg.get("parallel") or [1]   # v1 兼容：直配梯度
        elif mode == "baseline":
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
        else:
            raise ValueError(f"perf.mode 须 ∈ {sorted(_MODES)} 或省略（直配 parallel）")
        if mode is not None and cfg.get("parallel"):
            raise ValueError(f"perf.mode={mode} 模式下不要直接配 perf.parallel")
        if not (isinstance(parallel, list) and parallel
                and all(isinstance(v, int) and not isinstance(v, bool) and v >= 1
                        for v in parallel)):
            raise ValueError("perf.parallel 须为 ≥1 的整数数组")
        if cfg.get("number") is not None:
            logger.info("locust 忽略 perf.number（locust 按时长/用户数驱动）")

        seconds = _resolve_levels(cfg, parallel)
        spawn_rate = cfg.get("spawn_rate")
        output_dir = req.work_dir / "locust_raw"
        runs = []
        for users, secs in zip(parallel, seconds, strict=True):
            prefix = output_dir / f"locust_u{users}"
            rate = int(spawn_rate) if spawn_rate else users   # 缺省瞬时拉满
            runs.append({"users": users, "spawn_rate": rate, "seconds": float(secs),
                         "csv": str(prefix), "html": f"{prefix}_report.html"})

        return {
            "url": url, "model": model, "api_key": api_key,
            "dataset": dataset, "prompt": prompt,
            "stream": bool(cfg.get("stream", True)),
            "max_tokens": cfg.get("max_tokens"),
            "locustfile": str(req.work_dir / "locustfile.py"),
            "prompts_file": str(req.work_dir / "load" / "prompts.txt"),
            "runs": runs,
            "timeout": int(req.extra.get("timeout", DEFAULT_TIMEOUT)),
            "output_dir": str(output_dir),
        }

    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        cfg = self.build_config(req)
        self._write_load(cfg)
        await run_sync_in_thread(self._exec, cfg)
        return self.collect(req)

    def _write_load(self, cfg: dict) -> None:
        """负载落盘：locustfile + 逐行 prompt 文件（dataset/prompt 统一）。"""
        Path(cfg["locustfile"]).write_text(_LOCUSTFILE_TEMPLATE, encoding="utf-8")
        if cfg["dataset"]:
            n = _export_prompts(cfg["dataset"], Path(cfg["prompts_file"]))
            logger.info("locust 数据集负载导出 %d 条 prompt", n)
        else:
            path = Path(cfg["prompts_file"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(cfg["prompt"] + "\n", encoding="utf-8")

    def _exec(self, cfg: dict) -> None:
        """逐档 subprocess 执行 locust；任一档失败即抛错（后续档不跑）。"""
        binary = shutil.which("locust")
        if binary is None:
            raise RuntimeError("未找到 locust CLI——请 pip install 'aite[locust]'"
                               "（或 pip install locust）")
        out_dir = Path(cfg["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir.parent / "logs" / "locust.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ | {
            "PYTHONIOENCODING": "utf-8",
            "AITE_LOCUST_URL": cfg["url"],
            "AITE_LOCUST_MODEL": cfg["model"],
            "AITE_LOCUST_API_KEY": cfg["api_key"],
            "AITE_LOCUST_STREAM": "1" if cfg["stream"] else "0",
            "AITE_LOCUST_PROMPTS_FILE": cfg["prompts_file"],
        }
        if cfg["max_tokens"]:
            env["AITE_LOCUST_MAX_TOKENS"] = str(int(cfg["max_tokens"]))
        host = cfg["url"].rsplit("/chat/completions", 1)[0]
        lines = []
        for run in cfg["runs"]:
            args = [
                binary, "-f", cfg["locustfile"], "--headless", "--only-summary",
                "--csv", run["csv"], "--html", run["html"],
                "-u", str(run["users"]), "-r", str(run["spawn_rate"]),
                "-t", f"{int(run['seconds'])}s", "--host", host,
            ]
            lines.append(f"=== u{run['users']} × {run['seconds']}s ===")
            try:
                proc = subprocess.run(args, capture_output=True, env=env,
                                      timeout=int(run["seconds"]) + _LEVEL_GRACE)
            except subprocess.TimeoutExpired as exc:
                lines.append(f"LEVEL TIMEOUT u{run['users']}: {exc}")
                raise RuntimeError(
                    f"locust 档位超时（u{run['users']}，"
                    f"{int(run['seconds']) + _LEVEL_GRACE}s）") from exc
            lines.append((proc.stdout or b"").decode("utf-8", errors="replace"))
            lines.append((proc.stderr or b"").decode("utf-8", errors="replace"))
            if proc.returncode != 0:
                log_path.write_text("\n".join(lines), encoding="utf-8")
                raise RuntimeError(f"locust 执行失败（u{run['users']}，"
                                   f"exit={proc.returncode}），日志: {log_path}")
        log_path.write_text("\n".join(lines), encoding="utf-8")

    def collect(self, req: ToolRunRequest) -> ToolRunResult:
        """解析各档 locust_uN_stats/_failures CSV → 指标行 + 失败明细。"""
        cfg = self.build_config(req)   # 纯函数，重复调用拿 output_dir
        out = ToolRunResult()
        stats_files = sorted(Path(cfg["output_dir"]).glob("locust_u*_stats.csv"))
        if not stats_files:
            raise RuntimeError(f"未找到压测产出（{cfg['output_dir']}/locust_u*_stats.csv），"
                               "查看 logs/locust.log")
        for stats_path in stats_files:
            users = stats_path.name[len("locust_u"):-len("_stats.csv")]
            label = f"locust:u{users}"
            rows = list(csv.DictReader(stats_path.open(encoding="utf-8")))
            agg = next((r for r in rows if r.get("Type") == "Aggregated"), None)
            if agg is None:
                logger.warning("locust 产出缺少 Aggregated 行，跳过 %s", stats_path)
                continue
            ttft = next((r for r in rows if r.get("Type") == "TTFT"), None)

            def num(row: dict, key: str) -> float | None:
                try:
                    return float(row[key])
                except (KeyError, TypeError, ValueError):
                    return None

            total = num(agg, "Request Count") or 0
            failed = num(agg, "Failure Count") or 0
            metrics = {
                "latency_avg_ms": num(agg, "Average Response Time"),
                "latency_p95_ms": num(agg, "95%"),
                "qps": num(agg, "Requests/s"),
                "error_rate": (failed / total) if total else None,
            }
            if ttft is not None:   # 非流式无 TTFT 行 → 跳过（口径同 perf：None 不入库）
                metrics["ttft_ms"] = num(ttft, "Average Response Time")
                metrics["ttft_p95_ms"] = num(ttft, "95%")
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
            failures_path = stats_path.with_name(
                stats_path.name.replace("_stats.csv", "_failures.csv"))
            if failures_path.is_file():
                for frow in csv.DictReader(failures_path.open(encoding="utf-8")):
                    out.failures.append({
                        "dataset": label, "case": frow.get("Method", ""),
                        "detail": f"{frow.get('Error', '')}"
                                  f" ×{frow.get('Occurrences', '')}",
                    })
        base = Path(cfg["output_dir"]).parent
        for pattern in ("locust_raw/**/*.html", "locust_raw/**/*_stats.csv",
                        "logs/locust.log"):
            for p in sorted(base.glob(pattern)):
                out.report_files[p.name] = str(p)
        return out

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
