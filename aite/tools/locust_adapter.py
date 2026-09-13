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
- ``qps``：目标总 QPS（闭环封顶——服务端拖慢则实际 QPS 下降；user 数退化为
  最大并发上限）。与开环语义相对：精确开环 QPS 用 evalscope-perf 的
  ``perf.rate``（并发无界，过载真实显现）
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
# 越低越好（阈值方向 <=）；qps/output_tps 越高越好（与 evalscope-perf 口径一致）
_LOWER_IS_BETTER = {"ttft_ms", "ttft_p95_ms", "latency_avg_ms", "latency_p95_ms",
                    "itl_ms", "itl_p95_ms", "tpot_ms", "tpot_p95_ms",
                    "error_rate"}

def _num(row: dict, key: str) -> float | None:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def _new_bucket() -> dict:
    """秒级聚合桶（单一结构定义——history 行与 token 记录两条合并路径共用）。"""
    return {"ttft": [], "latency": [], "rps": 0.0, "errors": 0.0, "tok_s": 0.0,
            "tpot_avg": [], "tpot_p95": [], "itl_avg": [], "itl_p95": []}


def _history_series(output_dir: Path) -> list[dict]:
    """各档 _stats_history.csv → 时序 series（--csv-full-history，原生 1s 打点）。

    tok_s 来自各档 locust_uN_tokens.jsonl（请求完成时刻 epoch → 秒桶求和）。
    """
    series = []
    for history_path in sorted(output_dir.glob("*_stats_history.csv")):
        users = history_path.name[len("locust_u"):-len("_stats_history.csv")]
        tokens_by_sec = _tokens_by_sec(output_dir / f"locust_u{users}_tokens.jsonl")
        by_ts: dict[float, dict] = {}
        for hrow in csv.DictReader(history_path.open(encoding="utf-8")):
            rtype = hrow.get("Type")
            if rtype not in ("POST", "TTFT", "TPOT", "ITL", "LATENCY"):
                continue
            ts = _num(hrow, "Timestamp")
            if ts is None:
                continue
            b = by_ts.setdefault(ts, _new_bucket())
            if rtype == "POST":
                b["rps"] += _num(hrow, "Requests/s") or 0.0
                b["errors"] += _num(hrow, "Failures/s") or 0.0
            elif rtype == "LATENCY":
                for col in ("50%", "95%"):
                    if _num(hrow, col) is not None:
                        b["latency"].append(_num(hrow, col))
            elif rtype == "TTFT":
                for col in ("50%", "95%"):
                    if _num(hrow, col) is not None:
                        b["ttft"].append(_num(hrow, col))
            else:                      # TPOT / ITL：50% 列 → avg，95% 列 → p95
                key = rtype.lower()
                if _num(hrow, "50%") is not None:
                    b[f"{key}_avg"].append(_num(hrow, "50%"))
                if _num(hrow, "95%") is not None:
                    b[f"{key}_p95"].append(_num(hrow, "95%"))
        for sec, tokens in tokens_by_sec.items():   # token 吞吐并入秒桶
            b = by_ts.setdefault(sec, _new_bucket())
            b["tok_s"] += tokens
        if not by_ts:
            continue
        t0 = min(by_ts)
        points = [_point(ts, by_ts[ts], t0) for ts in sorted(by_ts)]
        series.append({"users": int(users), "run": 0,
                       "label": f"locust:u{users}", "points": points})
    return series


def _tokens_by_sec(path: Path) -> dict[int, float]:
    """locust_uN_tokens.jsonl → {完成时刻秒: tokens 和}（无文件返回空）。"""
    out: dict[int, float] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        sec = int(rec.get("t") or 0)
        out[sec] = out.get(sec, 0.0) + float(rec.get("tokens") or 0)
    return out


def _combined_series(output_dir: Path) -> list[dict]:
    """绝对时间轴（stats_history 的 Timestamp 为 epoch 秒，各档进程同源时钟）
    合并全部档位 → 一条完整阶梯曲线（users 随档位阶梯、指标连续）。"""
    points = []
    for history_path in sorted(output_dir.glob("*_stats_history.csv")):
        users = int(history_path.name[len("locust_u"):-len("_stats_history.csv")])
        for hrow in csv.DictReader(history_path.open(encoding="utf-8")):
            if hrow.get("Type") != "POST":
                continue
            ts = _num(hrow, "Timestamp")
            if ts is None:
                continue
            points.append((ts, users, _num(hrow, "Requests/s") or 0.0,
                           _num(hrow, "Failures/s") or 0.0,
                           _num(hrow, "95%")))
    if not points:
        return []
    points.sort(key=lambda r: r[0])
    t0 = points[0][0]
    tok_by_sec = _tokens_by_sec_all(output_dir)
    return [{"t": round(ts - t0, 3), "users": users, "rps": round(rps, 3),
             "errors": int(fails), "latency_p95": round(p95, 1) if p95 else None,
             "tok_s": round(tok_by_sec.get(ts, 0.0), 1) or None}
            for ts, users, rps, fails, p95 in points]


def _tokens_by_sec_all(output_dir: Path) -> dict[int, float]:
    """全档 tokens 记录合并 → {epoch 秒: tokens 和}。"""
    out: dict[int, float] = {}
    for path in output_dir.glob("*_tokens.jsonl"):
        for sec, tokens in _tokens_by_sec(path).items():
            out[sec] = out.get(sec, 0.0) + tokens
    return out


def _point(ts: float, b: dict, t0: float) -> dict:
    """秒级时序点：POST 行 rps/延迟分位 + TTFT/TPOT/ITL 行分位 + token 吞吐。"""
    return {
        "t": round(ts - t0, 3),
        "ttft_avg": round(sum(b["ttft"]) / len(b["ttft"]), 1) if b["ttft"] else None,
        "ttft_p95": round(max(b["ttft"]), 1) if b["ttft"] else None,
        "latency_avg": round(sum(b["latency"]) / len(b["latency"]), 1) if b["latency"] else None,
        "latency_p95": round(max(b["latency"]), 1) if b["latency"] else None,
        "tpot_avg": round(sum(b["tpot_avg"]) / len(b["tpot_avg"]), 1) if b["tpot_avg"] else None,
        "tpot_p95": round(max(b["tpot_p95"]), 1) if b["tpot_p95"] else None,
        "itl_avg": round(sum(b["itl_avg"]) / len(b["itl_avg"]), 1) if b["itl_avg"] else None,
        "itl_p95": round(max(b["itl_p95"]), 1) if b["itl_p95"] else None,
        "rps": round(b["rps"], 3),
        "tok_s": round(b["tok_s"], 1) if b.get("tok_s") else None,
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

from locust import HttpUser, constant, constant_pacing, events, task

URL = os.environ["AITE_LOCUST_URL"]
MODEL = os.environ["AITE_LOCUST_MODEL"]
API_KEY = os.environ.get("AITE_LOCUST_API_KEY", "none")
STREAM = os.environ.get("AITE_LOCUST_STREAM", "1") == "1"
MAX_TOKENS = os.environ.get("AITE_LOCUST_MAX_TOKENS")
PROMPTS_FILE = os.environ["AITE_LOCUST_PROMPTS_FILE"]
TOKENS_FILE = os.environ.get("AITE_LOCUST_TOKENS_FILE")
# QPS 限速（可选）：闭环封顶语义——服务端拖慢时实际 QPS 随之下降；
# user 数退化为最大并发上限。未设置 = 并发模式（无间隔连发）。
QPS = os.environ.get("AITE_LOCUST_QPS")


def _record_tokens(tokens: int, gen_ms: float, prompt_tokens=None):
    """请求级 token 产出记录（output_tps / tok_s / 长度均值的数据源）。"""
    if not TOKENS_FILE or tokens <= 0:
        return
    rec = {"t": time.time(), "tokens": tokens, "gen_ms": round(gen_ms, 1)}
    if prompt_tokens:
        rec["prompt_tokens"] = int(prompt_tokens)
    with open(TOKENS_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\\n")

with open(PROMPTS_FILE, encoding="utf-8") as fh:
    PROMPTS = [line for line in (l.strip() for l in fh) if line]


def _wait_time():
    if QPS:
        return constant_pacing(1.0 / float(QPS), constant_total_pacing=True)
    return constant(0)


class ChatUser(HttpUser):
    """闭环压测用户：并发模式（连发）或 QPS 限速模式（封顶）。"""

    wait_time = _wait_time()
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

    def _fire(self, rtype: str, ms: float):
        # TTFT/ITL/TPOT/TPS 独立事件：不混入延迟统计，collect 按行名取数
        events.request.fire(request_type=rtype, name="POST /chat/completions",
                            response_time=ms, response_length=0,
                            exception=None, context={})

    @task
    def chat(self):
        payload = {"model": MODEL, "stream": STREAM,
                   "stream_options": {"include_usage": True},
                   "messages": [{"role": "user", "content": self._next_prompt()}]}
        if MAX_TOKENS:
            payload["max_tokens"] = int(MAX_TOKENS)
        t0 = time.perf_counter()
        # stream=True：真流式观测（TTFT/ITL/TPOT 的前提）——False 时响应
        # 完整缓冲后一次读取，所有到达间隔为 0（观测全废）
        with self.client.post(URL, json=payload, name="POST /chat/completions",
                              catch_response=True, timeout=300,
                              stream=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
                return
            if STREAM:
                self._stream_metrics(resp, t0)
            else:
                self._json_metrics(resp, t0)
            resp.success()

    def _stream_metrics(self, resp, t0: float):
        """流式逐块采集：TTFT / ITL（块间隔）/ TPOT / tok/s 记录。

        token 数取 usage.completion_tokens（端点回传 usage 时精确），否则以
        chunk 数近似（主流推理服务 1 chunk ≈ 1 token）。
        ITL/TPOT 仅在真流式（逐 token 到达）时产生：网关缓冲型端点整个响应
        瞬间到达，块间隔为微秒级噪声——不满足逐 token 特征的不 fire（宁缺毋错）。
        """
        ttft_ms = None
        prev_ms = None
        chunk_count = 0
        completion_tokens = None
        prompt_tokens = None
        for raw in resp.iter_lines():
            if not raw.startswith(b"data: "):
                continue
            now_ms = (time.perf_counter() - t0) * 1000.0
            if ttft_ms is None:
                ttft_ms = now_ms
            elif prev_ms is not None and now_ms - prev_ms >= 1.0:
                # 间隔 <1ms = 读缓冲批量解包噪声（非真实 token 到达），不上报
                self._fire("ITL", now_ms - prev_ms)
            prev_ms = now_ms
            chunk_count += 1
            data = raw[6:].strip()
            if data == b"[DONE]":
                break
            if b'"usage"' in data:
                try:
                    usage = json.loads(data).get("usage") or {}
                    if usage.get("completion_tokens"):
                        completion_tokens = int(usage["completion_tokens"])
                    if usage.get("prompt_tokens"):
                        prompt_tokens = int(usage["prompt_tokens"])
                except ValueError:
                    pass
        if ttft_ms is None or prev_ms is None:
            return
        self._fire("LATENCY", prev_ms)     # 端到端 = 最后一块到达（stream 下
        self._fire("TTFT", ttft_ms)        #   resp.elapsed 只是 TTFB 口径）
        tokens = completion_tokens if completion_tokens else max(chunk_count - 1, 1)
        gen_ms = prev_ms - ttft_ms            # 生成段时长（首 token 之后）
        if gen_ms > 0 and tokens > 1:
            _record_tokens(tokens, gen_ms, prompt_tokens)          # 吞吐+长度数据源
            # 真流式判定（与入库失真门槛同口径）：均摊 <1ms/token = 批量到达，
            # TPOT 为噪声不 fire
            if gen_ms / (tokens - 1) >= 1.0:
                self._fire("TPOT", gen_ms / (tokens - 1))          # ms / token

    def _json_metrics(self, resp, t0: float):
        """非流式：整段均摊口径的 TPOT（无 TTFT/ITL，与 evalscope 一致）。"""
        total_ms = (time.perf_counter() - t0) * 1000.0
        try:
            data = resp.json()
        except ValueError:
            return
        usage = data.get("usage") or {}
        tokens = int(usage.get("completion_tokens") or 0)
        if tokens > 0 and total_ms > 0:
            self._fire("TPOT", total_ms / tokens)
            _record_tokens(tokens, total_ms, usage.get("prompt_tokens"))
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

        qps = cfg.get("qps")
        if qps is not None and (not isinstance(qps, (int, float))
                                or isinstance(qps, bool) or qps <= 0):
            raise ValueError("perf.qps 须为 >0 的数字（目标总 QPS，闭环封顶）")

        spawn_rate = cfg.get("spawn_rate")
        output_dir = req.work_dir / "locust_raw"
        runs = []
        for users, secs in zip(parallel, self._level_seconds(plan, parallel), strict=True):
            prefix = output_dir / f"locust_u{users}"
            rate = int(spawn_rate) if spawn_rate else users   # 缺省瞬时拉满
            runs.append({"users": users, "spawn_rate": rate, "seconds": float(secs),
                         "csv": str(prefix), "html": f"{prefix}_report.html",
                         "tokens_file": str(output_dir / f"locust_u{users}_tokens.jsonl")})

        return {
            "url": url, "model": model, "api_key": api_key,
            "dataset": dataset, "prompt": prompt, "synthetic": synthetic,
            "stream": bool(cfg.get("stream", True)),
            "qps": float(qps) if qps is not None else None,
            "max_tokens": cfg.get("max_tokens"),
            "locustfile": str(req.work_dir / "locustfile.py"),
            "prompts_file": str(req.work_dir / "load" / "prompts.txt"),
            "runs": runs,
            "timeout": int(req.extra.get("timeout", 3600)),
            "output_dir": str(output_dir),
            "tokens_files": sorted({r["tokens_file"] for r in runs}),
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
        if cfg.get("qps"):
            env_extra["AITE_LOCUST_QPS"] = str(cfg["qps"])
        host = cfg["url"].rsplit("/chat/completions", 1)[0]
        for i, run in enumerate(cfg["runs"]):
            env_extra = dict(env_extra, AITE_LOCUST_TOKENS_FILE=run["tokens_file"])
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

        # output_tps（全局口径）/ 平均输入输出长度：tokens 记录聚合
        tokens_total = gen_seconds = 0.0
        prompt_lens: list[float] = []
        completion_lens: list[float] = []
        for tf in Path(cfg["output_dir"]).glob("*_tokens.jsonl"):
            for line in tf.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                tokens_total += float(rec.get("tokens") or 0)
                gen_seconds += float(rec.get("gen_ms") or 0) / 1000.0
                if rec.get("prompt_tokens"):
                    prompt_lens.append(float(rec["prompt_tokens"]))
                # tokens 为 completion 的别名（记录主键）
                completion = rec.get("completion_tokens") or rec.get("tokens")
                if completion:
                    completion_lens.append(float(completion))
        if gen_seconds > 0 and tokens_total > 0:
            global_tps = round(tokens_total / gen_seconds, 2)
            thr = req.metric_thresholds.get("output_tps")
            out.rows.append(ToolMetricRow(
                dataset="locust:total", metric="output_tps", value=global_tps,
                count=int(tokens_total), threshold=thr, higher_is_better=True,
                passed=(global_tps >= thr) if thr is not None else None))
        length_rows = {
            "avg_input_tokens": (prompt_lens, len(prompt_lens)),
            "avg_output_tokens": (completion_lens, len(completion_lens)),
        }
        for metric, (lens, count) in length_rows.items():
            if lens:
                out.rows.append(ToolMetricRow(
                    dataset="locust:total", metric=metric,
                    value=round(sum(lens) / len(lens), 1), count=count,
                    threshold=req.metric_thresholds.get(metric),
                    higher_is_better=True))

        timeseries = {"engine": self.name, "bucket_seconds": 1.0,
                      "series": _history_series(Path(cfg["output_dir"])),
                      "combined": _combined_series(Path(cfg["output_dir"])),
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
            if any(r.get("Type") in ("TTFT", "ITL", "TPOT") for r in rows):
                # 自定义事件在、请求行不在 = 任务内抛异常（未标记 success/failure
                # 时 locust 不计入请求统计），典型根因见 logs/locust.log 的 Traceback
                logger.warning(
                    "locust %s: 有自定义指标事件但无请求统计——locust 任务内抛异常"
                    "（如签名不匹配/字段缺失），Traceback 见 logs/locust.log",
                    stats_path.name)
            else:
                logger.warning("locust 产出缺少 POST/Aggregated 行，跳过 %s",
                               stats_path)
            return
        by_type: dict[str, dict] = {}
        for r in rows:
            rtype = r.get("Type")
            if rtype in ("TTFT", "ITL", "TPOT", "LATENCY") and rtype not in by_type:
                by_type[rtype] = r
        total = _num(agg, "Request Count") or 0
        failed = _num(agg, "Failure Count") or 0
        metrics = {
            "qps": _num(agg, "Requests/s"),
            "error_rate": (failed / total) if total else None,
        }
        # 延迟：LATENCY 事件行（stream=True 下 POST 原生统计≈TTFB 口径），
        # 无 LATENCY 行兜底 POST/Aggregated
        lat_row = by_type.get("LATENCY") or agg
        if lat_row is not None:
            metrics["latency_avg_ms"] = _num(lat_row, "Average Response Time")
            metrics["latency_p95_ms"] = _num(lat_row, "95%")
        ttft = by_type.get("TTFT")
        if ttft is not None:   # 非流式无 TTFT 行 → 跳过（口径同 perf：None 不入库）
            metrics["ttft_ms"] = _num(ttft, "Average Response Time")
            metrics["ttft_p95_ms"] = _num(ttft, "95%")
        itl = by_type.get("ITL")
        if itl is not None:
            metrics["itl_ms"] = _num(itl, "Average Response Time")
            metrics["itl_p95_ms"] = _num(itl, "95%")
        tpot = by_type.get("TPOT")
        if tpot is not None:
            metrics["tpot_ms"] = _num(tpot, "Average Response Time")
            metrics["tpot_p95_ms"] = _num(tpot, "95%")
        # output_tps（全局口径）由 run_tool_suite 后置注入（tokens 文件聚合）
        # 非真流式端点检测：TTFT ≈ 端到端延迟 = SSE 被网关缓冲（整个响应
        # 完成时一次性到达）——TPOT/ITL 原理性不可测，TTFT 实为端到端延迟
        if (metrics.get("ttft_ms") and metrics.get("latency_avg_ms")
                and abs(metrics["ttft_ms"] - metrics["latency_avg_ms"])
                < 0.01 * metrics["latency_avg_ms"]):
            logger.warning(
                "locust %s: TTFT ≈ 端到端延迟——端点 SSE 被网关缓冲（非真流式），"
                "TPOT/ITL 不可测；TTFT 数值实为端到端延迟", label)
        # ITL/TPOT 失真门槛（宁缺毋错）：高并发下客户端读缓冲批量到达 SSE 行，
        # 块间隔被压缩到微秒级（avg < 1ms）——此时 ITL/TPOT 不可信，不入库
        degraded = [name for name in ("itl_ms", "itl_p95_ms", "tpot_ms", "tpot_p95_ms")
                    if metrics.get(name) is not None and metrics[name] < 1.0]
        for name in degraded:
            metrics.pop(name)
        if degraded:
            logger.warning("locust %s: ITL/TPOT 均值 < 1ms（高并发下读缓冲批量到达"
                           "SSE 行，客户端观测失真），本轮不入库", label)
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
                        "itl_ms", "itl_p95_ms", "tpot_ms", "tpot_p95_ms",
                        "qps", "output_tps", "avg_input_tokens", "avg_output_tokens",
                        "error_rate"],
            "tags": ["perf", "locust"],
            "few_shot_num": None,
        }]
