"""locust 适配器（通用 HTTP 压测，evalscope-perf 的并列备选）。

suite yaml 写 ``engine: locust`` 即路由到本适配器；压测委托 locust 子进程
（gevent 协程 + 分布式 worker 支持），aite 负责编排与统一入库。与
evalscope-perf 的分工：后者 LLM 原生指标（TPOT/ITL/吞吐分档）更全，本适配器
面向非 OpenAI 兼容端点改造 / 极端规模分布式 / 通用 HTTP 打点场景。

**复用 suite 的 ``perf`` 配置段**（与 evalscope-perf 同键同语义）：

- 负载：``dataset``（jsonl/csv 逐行 prompt）/ ``prompt``（固定提示词）/
  ``synthetic``（确定性合成）三选一
- 模式：``baseline`` / ``ramp`` / ``soak`` / 省略 mode 直配 ``parallel``；
  **probe 不支持**（SLA 二分搜索是 evalscope perf 专有，配置即报错）
- 时长：``duration``（整场总时长，多档均分）/ ``level_duration``（每档秒数）
  二选一——locust 每档是独立 ``-t`` 进程，到点自退出，无看门狗
- ``spawn_rate``：每档用户拉起速率（用户/秒），缺省 = 档位并发（瞬时拉满）
- ``qps``：闭环封顶目标 QPS（constant_total_pacing）——服务端拖慢时实际
  QPS 随之下降；精确开环 QPS 用 evalscope-perf 的 ``perf.rate``
- ``jitter``：到达抖动秒数（并发模式打散同步波次；缺省 0.2s，显式 0 关闭）；
  ``arrival``：到达模型
  poisson（泊松到达）/spike（浪涌）/custom（自定义 QPS 曲线，现网流量形状）
  ——全局节拍器接管限速，并发数退化为在途上限；``extra_body``：请求体扩展
  （vLLM ignore_eos/min_tokens 定长输出等）；``number`` 对 locust 无语义，
  配了忽略

**采集通道（全部走 jsonl 文件，不经 locust stats）**：每档一个
``locust_uN_metrics.jsonl``，每请求一条 {t, ok, error?, latency_ms, ttft_ms,
tpot_ms, itl_avg, itl_p95, itl_n, tokens, gen_ms, prompt_tokens}——collect 从
jsonl 聚合档级指标、秒级时序与全场阶梯曲线（combined，按各档进程墙钟拼接）。
locust stats 通道只保留 POST 原生行（TTFT/
ITL/TPOT 自定义事件会污染 Aggregated 且 int 取整损失精度，故不进 stats）；
请求经 stream=True 真流式读取，端到端延迟单独记录。
"""

from __future__ import annotations

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
    expand_ramp,
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

_LOCUSTFILE_TEMPLATE = '''# 由 aite locust 适配器生成（勿手改；regenerate per run）
import json
import os
import random
import time

import gevent
from gevent.lock import Semaphore
from locust import HttpUser, constant, constant_pacing, events, task

URL = os.environ["AITE_LOCUST_URL"]
MODEL = os.environ["AITE_LOCUST_MODEL"]
API_KEY = os.environ.get("AITE_LOCUST_API_KEY", "none")
STREAM = os.environ.get("AITE_LOCUST_STREAM", "1") == "1"
MAX_TOKENS = os.environ.get("AITE_LOCUST_MAX_TOKENS")
PROMPTS_FILE = os.environ["AITE_LOCUST_PROMPTS_FILE"]
METRICS_FILE = os.environ["AITE_LOCUST_METRICS_FILE"]
# QPS 限速（可选）：闭环封顶语义——服务端拖慢时实际 QPS 随之下降；
# user 数退化为最大并发上限。未设置 = 并发模式（无间隔连发）。
QPS = os.environ.get("AITE_LOCUST_QPS")
# 到达抖动（秒）：并发闭环模式每请求前随机等待 U(0, JITTER)——打散同步波次
# （8 user 同相起跑会被服务端批处理正反馈锁死：TTFT 尖峰周期化 + 吞吐虚低）
JITTER = float(os.environ.get("AITE_LOCUST_JITTER", "0") or 0)
# 到达模型（可选，配置后接管限速）：poisson（泊松到达，需 QPS）/ spike（浪涌）/
# custom（自定义 QPS 曲线，模拟现网流量形状）——配置校验见 aite 侧 build_config
ARRIVAL = json.loads(os.environ.get("AITE_LOCUST_ARRIVAL") or "null") or {}
# 请求体扩展（可选）：JSON object 合并进 payload——如 vLLM 定长输出
# {"min_tokens": 256, "ignore_eos": true}（一般 OpenAI 兼容端点不认识会 4xx）
EXTRA_BODY = json.loads(os.environ.get("AITE_LOCUST_EXTRA_BODY") or "{}")

with open(PROMPTS_FILE, encoding="utf-8") as fh:
    PROMPTS = [line for line in (l.strip() for l in fh) if line]


def _record(rec: dict):
    """请求级指标落盘（jsonl）——aite 侧统计与时序的唯一数据源。"""
    with open(METRICS_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\\n")


def _rate_at(now: float) -> float:
    """到达模型在时刻 now 的全局目标 QPS。"""
    mode = ARRIVAL.get("mode")
    if mode == "poisson":
        return float(QPS)
    if mode == "spike":
        sp = ARRIVAL["spike"]
        in_window = (now % sp["period"]) < sp["spike_duration"]
        return float(sp["spike_qps"] if in_window else sp["base_qps"])
    profile = ARRIVAL["profile"]            # custom：[[t, qps], ...] 阶梯
    span = profile[-1][0]
    if ARRIVAL.get("repeat") and span > 0:
        now = now % span
    rate = float(profile[0][1])
    for t, q in profile:
        if now >= t:
            rate = float(q)
        else:
            break
    return rate


class _Pacer:
    """全局到达节拍器：全部 user 共享一把锁，按到达模型发放请求时隙。

    与 per-user constant_pacing 不同，admission 时刻集中计算间隔——泊松模式
    逐时隙指数随机（真全局泊松过程），浪涌/自定义按当前目标速率均匀发牌。
    并发数（-u）退化为在途上限：user 全忙时请求在节拍器前排队。
    """

    def __init__(self):
        self._next = time.time()
        self._sem = Semaphore()

    def wait(self) -> float:
        with self._sem:
            now = time.time()
            delay = self._next - now
            rate = max(_rate_at(now), 1e-6)
            step = (random.expovariate(rate) if ARRIVAL.get("mode") == "poisson"
                    else 1.0 / rate)
            self._next = max(self._next, now) + step
        return max(delay, 0.0)


_PACER = _Pacer() if ARRIVAL.get("mode") in ("poisson", "spike", "custom") else None


def _wait_time():
    if _PACER is not None:
        return lambda self: _PACER.wait()
    if QPS:
        return constant_pacing(1.0 / float(QPS), constant_total_pacing=True)
    if JITTER > 0:
        return lambda self: random.uniform(0, JITTER)
    return constant(0)


class ChatUser(HttpUser):
    """闭环压测用户：并发模式（连发）或 QPS 限速模式（封顶）。"""

    wait_time = _wait_time()
    host = os.environ.get("AITE_LOCUST_HOST")

    def on_start(self):
        self.client.headers.update({
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            # requests 缺省携带 Accept-Encoding: gzip——SSE 过压缩网关会被
            # 重缓冲（首包延迟/合批）；aiohttp（evalscope）缺省不请求压缩
            "Accept-Encoding": "identity",
        })
        self._i = -1

    def _next_prompt(self):
        self._i = (self._i + 1) % len(PROMPTS)
        return PROMPTS[self._i]

    @task
    def chat(self):
        payload = {"model": MODEL, "stream": STREAM,
                   "stream_options": {"include_usage": True},
                   "messages": [{"role": "user", "content": self._next_prompt()}]}
        if MAX_TOKENS:
            payload["max_tokens"] = int(MAX_TOKENS)
        if EXTRA_BODY:
            payload.update(EXTRA_BODY)
        t0 = time.perf_counter()
        # stream=True：真流式观测（TTFT/ITL/TPOT 的前提）——False 时响应
        # 完整缓冲后一次读取，所有到达间隔为 0（观测全废）
        with self.client.post(URL, json=payload, name="POST /chat/completions",
                              catch_response=True, timeout=300,
                              stream=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
                _record({"t": time.time(), "ok": 0,
                         "error": f"HTTP {resp.status_code}"})
                return
            rec = (self._stream_metrics(resp, t0) if STREAM
                   else self._json_metrics(resp, t0))
            rec["t"] = time.time()
            rec["ok"] = 1
            _record(rec)
            resp.success()


    def _stream_metrics(self, resp, t0: float) -> dict:
        """流式逐块采集：TTFT 首包、ITL 逐块间隔、TPOT（生成段 / (tokens-1)）。

        token 数取 usage.completion_tokens（端点回传 usage 时精确），否则以
        chunk 数近似（主流推理服务 1 chunk ≈ 1 token）。
        """
        ttft_ms = None
        prev_ms = None
        chunk_count = 0
        completion_tokens = None
        prompt_tokens = None
        itls = []
        # chunk_size=1 逐字节读：iter_lines 缺省 512B 缓冲会把多个 SSE 事件
        # 合批（实测 ITL 采样密度仅 0.35、均值虚高 ~3 倍），逐字节读保留
        # 真实到达粒度，与 evalscope（aiohttp 逐网络包）同口径
        for raw in resp.iter_lines(chunk_size=1):
            if not raw.startswith(b"data: "):
                continue
            now_ms = (time.perf_counter() - t0) * 1000.0
            if ttft_ms is None:
                ttft_ms = now_ms
            elif prev_ms is not None:
                # 全量入样（含合批 0 间隔）——与 evalscope 逐 token 口径一致；
                # 网关缓冲型端点（整包到达）的假流式由 collect 侧守卫拦截
                itls.append(now_ms - prev_ms)
            prev_ms = now_ms
            chunk_count += 1
            data = raw[6:].strip()
            if data == b"[DONE]":
                # 不 break：继续读完尾部空行直至流自然结束——提前退出会让
                # urllib3 判定响应未消费完而弃用连接，每请求重付 TCP+TLS
                # 握手（TTFT 平抬 ~100ms + 服务端连接 churn）
                continue
            if b'"usage"' in data:
                try:
                    usage = json.loads(data).get("usage") or {}
                    if usage.get("completion_tokens"):
                        completion_tokens = int(usage["completion_tokens"])
                    if usage.get("prompt_tokens"):
                        prompt_tokens = int(usage["prompt_tokens"])
                except ValueError:
                    pass
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        tokens = completion_tokens if completion_tokens else max(chunk_count - 1, 1)
        gen_ms = (prev_ms - ttft_ms) if (ttft_ms is not None and prev_ms is not None) else 0.0
        tpot_ms = round(gen_ms / (tokens - 1), 1) if tokens > 1 and gen_ms > 0 else None
        itl_avg = round(sum(itls) / len(itls), 1) if itls else None
        itl_p95 = round(sorted(itls)[int(0.95 * (len(itls) - 1))], 1) if itls else None
        return {"latency_ms": latency_ms,
                "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
                "tpot_ms": tpot_ms, "itl_avg": itl_avg, "itl_p95": itl_p95,
                "itl_n": len(itls), "tokens": tokens, "gen_ms": round(gen_ms, 1),
                "prompt_tokens": prompt_tokens}

    def _json_metrics(self, resp, t0: float) -> dict:
        """非流式：整段均摊口径的 TPOT（无 TTFT/ITL，与 evalscope 一致）。"""
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        try:
            data = resp.json()
        except ValueError:
            return {"latency_ms": latency_ms, "ttft_ms": None, "tpot_ms": None,
                    "itl_avg": None, "itl_p95": None, "itl_n": 0,
                    "tokens": 0, "gen_ms": 0.0, "prompt_tokens": None}
        usage = data.get("usage") or {}
        tokens = int(usage.get("completion_tokens") or 0)
        tpot_ms = round(latency_ms / tokens, 1) if tokens > 0 else None
        return {"latency_ms": latency_ms, "ttft_ms": None, "tpot_ms": tpot_ms,
                "itl_avg": None, "itl_p95": None, "itl_n": 0,
                "tokens": tokens, "gen_ms": round(latency_ms, 1),
                "prompt_tokens": int(usage.get("prompt_tokens") or 0) or None}
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

        jitter = cfg.get("jitter")
        if jitter is not None and (not isinstance(jitter, (int, float))
                                   or isinstance(jitter, bool) or jitter < 0):
            raise ValueError("perf.jitter 须为 ≥0 的数字（秒，到达抖动——打散同步波次）")
        if jitter is None:
            jitter = 0.0   # 缺省 0 = 零思考时间在途钉满，与 evalscope 形态同构
                           # （keep-alive 修复后到达自然错开，无需抖动护航；
                           #  突发场景显式配 jitter/arrival 刻意制造）
        arrival = self._resolve_arrival(cfg, qps)
        if arrival is not None and arrival["mode"] != "poisson" and qps is not None:
            logger.info("locust arrival=%s 接管限速，perf.qps=%s 忽略",
                        arrival["mode"], qps)
        extra_body = cfg.get("extra_body") or {}
        if not isinstance(extra_body, dict):
            raise ValueError("perf.extra_body 须为对象（合并进请求体，"
                             "如 vLLM 的 {min_tokens, ignore_eos} 定长输出）")

        spawn_rate = cfg.get("spawn_rate")
        output_dir = req.work_dir / "locust_raw"
        # 重复并发档（如 [4, 4] 稳定性复跑）共用此前缀会互相覆盖/append 合并
        # → 全档加执行序后缀消歧（collect 侧 locust_u<users>_<序> → u<users>#<序>）
        duplicated = len(parallel) != len(set(parallel))
        runs = []
        for i, (users, secs) in enumerate(
                zip(parallel, self._level_seconds(plan, parallel), strict=True), 1):
            stem = f"locust_u{users}" + (f"_{i}" if duplicated else "")
            prefix = output_dir / stem
            rate = int(spawn_rate) if spawn_rate else users   # 缺省瞬时拉满
            runs.append({"users": users, "spawn_rate": rate, "seconds": float(secs),
                         "csv": str(prefix), "html": f"{prefix}_report.html",
                         "metrics_file": str(output_dir / f"{stem}_metrics.jsonl")})

        return {
            "url": url, "model": model, "api_key": api_key,
            "dataset": dataset, "prompt": prompt, "synthetic": synthetic,
            "stream": bool(cfg.get("stream", True)),
            "qps": float(qps) if qps is not None else None,
            "max_tokens": cfg.get("max_tokens"),
            "jitter": float(jitter) if jitter else 0.0,
            "arrival": arrival, "extra_body": extra_body,
            "locustfile": str(req.work_dir / "locustfile.py"),
            "prompts_file": str(req.work_dir / "load" / "prompts.txt"),
            "runs": runs,
            "metrics_files": sorted({r["metrics_file"] for r in runs}),
            "timeout": int(req.extra.get("timeout", 3600)),
            "output_dir": str(output_dir),
        }

    @staticmethod
    def _resolve_arrival(cfg: dict, qps) -> dict | None:
        """perf.arrival 到达模型校验 → 模板环境 JSON（locust 专有）。

        poisson 泊松到达（需 qps 作平均速率）；spike 浪涌（基线与峰值 QPS 按
        周期切换）；custom 自定义 QPS 阶梯曲线（模拟现网流量形状，可 repeat
        循环）。配置后由全局节拍器接管限速（qps 封顶不再生效）。
        """
        raw = cfg.get("arrival")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("perf.arrival 须为对象（mode: poisson | spike | custom）")
        mode = raw.get("mode")
        if mode == "poisson":
            if qps is None:
                raise ValueError("arrival.mode=poisson 需要 perf.qps（平均到达率）")
            return {"mode": "poisson"}
        if mode == "spike":
            sp = raw.get("spike")
            if not isinstance(sp, dict):
                raise ValueError("arrival.spike 须为对象 {base_qps, spike_qps, "
                                 "period, spike_duration}")
            keys = ("base_qps", "spike_qps", "period", "spike_duration")
            if any(not isinstance(sp.get(k), (int, float)) or isinstance(sp.get(k), bool)
                   or sp.get(k) <= 0 for k in keys):
                raise ValueError(f"arrival.spike 的 {keys} 须为 >0 的数字")
            if sp["spike_duration"] > sp["period"]:
                raise ValueError("arrival.spike.spike_duration 须 ≤ period（浪涌窗口）")
            return {"mode": "spike",
                    "spike": {k: float(sp[k]) for k in keys}}
        if mode == "custom":
            profile = raw.get("profile")
            if (not isinstance(profile, list) or len(profile) < 1
                    or not all(isinstance(pt, (list, tuple)) and len(pt) == 2
                               and isinstance(pt[0], (int, float))
                               and not isinstance(pt[0], bool) and pt[0] >= 0
                               and isinstance(pt[1], (int, float))
                               and not isinstance(pt[1], bool) and pt[1] > 0
                               for pt in profile)):
                raise ValueError("arrival.custom.profile 须为 [[t 秒, qps], ...] "
                                 "数组（t ≥ 0 升序，qps > 0）")
            times = [pt[0] for pt in profile]
            if times != sorted(times):
                raise ValueError("arrival.custom.profile 的 t 须升序")
            return {"mode": "custom",
                    "profile": [[float(t), float(q)] for t, q in profile],
                    "repeat": bool(raw.get("repeat"))}
        raise ValueError("perf.arrival.mode 须为 poisson | spike | custom")

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
        base_env = {
            "PYTHONUTF8": "1",
            "AITE_LOCUST_URL": cfg["url"],
            "AITE_LOCUST_MODEL": cfg["model"],
            "AITE_LOCUST_API_KEY": cfg["api_key"],
            "AITE_LOCUST_STREAM": "1" if cfg["stream"] else "0",
            "AITE_LOCUST_PROMPTS_FILE": cfg["prompts_file"],
        }
        if cfg["max_tokens"]:
            base_env["AITE_LOCUST_MAX_TOKENS"] = str(int(cfg["max_tokens"]))
        if cfg["qps"]:
            base_env["AITE_LOCUST_QPS"] = str(cfg["qps"])
        base_env["AITE_LOCUST_JITTER"] = str(cfg["jitter"])   # 缺省 0.2，显式 0 关闭
        if cfg["arrival"]:
            base_env["AITE_LOCUST_ARRIVAL"] = json.dumps(cfg["arrival"])
        if cfg["extra_body"]:
            base_env["AITE_LOCUST_EXTRA_BODY"] = json.dumps(cfg["extra_body"])
        host = cfg["url"].rsplit("/chat/completions", 1)[0]
        for i, run in enumerate(cfg["runs"]):
            env_extra = dict(base_env, AITE_LOCUST_METRICS_FILE=run["metrics_file"])
            args = [
                binary, "-f", cfg["locustfile"], "--headless", "--only-summary",
                "--csv", run["csv"],
                "--html", run["html"],
                "-u", str(run["users"]), "-r", str(run["spawn_rate"]),
                "-t", f"{int(run['seconds'])}s", "--host", host,
            ]
            outcome, out, err = run_tool_subprocess(
                args, log_path=log_path, timeout=int(run["seconds"]) + _LEVEL_GRACE,
                env_extra=env_extra, append_log=i > 0,
                log_prefix=f"=== u{run['users']} × {run['seconds']}s ===\n")
            if outcome is None:
                raise RuntimeError(
                    f"locust 档位超时（u{run['users']}，"
                    f"{int(run['seconds']) + _LEVEL_GRACE}s）")
            if outcome != 0:
                raise RuntimeError(f"locust 执行失败（u{run['users']}，exit={outcome}），"
                                   f"日志: {log_path}\n{out[-2000:]}")

    # ---------------------------------------------------------------- collect

    def collect(self, req: ToolRunRequest) -> ToolRunResult:
        """解析各档 locust_uN_metrics.jsonl → 指标行 + 失败明细 + 秒级时序。"""
        cfg = self.build_config(req)   # 纯函数，重复调用拿 output_dir
        out = ToolRunResult()
        metrics_files = sorted(Path(cfg["output_dir"]).glob("locust_u*_metrics.jsonl"))
        if not metrics_files:
            raise RuntimeError(f"未找到压测产出（{cfg['output_dir']}/locust_u*_metrics.jsonl），"
                               "查看 logs/locust.log")
        all_recs: list[dict] = []
        for mf in metrics_files:
            # 档名 locust_u<users> 或重复档消歧 locust_u<users>_<执行序>
            stem = mf.name[len("locust_u"):-len("_metrics.jsonl")]
            users = int(stem.partition("_")[0])
            label = "locust:u" + stem.replace("_", "#")
            recs = self._read_recs(mf)
            self._collect_level(req, out, recs, label)
            self._collect_failures(out, recs, label)
            all_recs.extend({**r, "users": users} for r in recs)

        # ---- 秒级时序（请求完成时刻 epoch → 1s 桶）----
        series, combined = self._series_from_recs(all_recs)
        from aite.tools import serialize_summaries  # 懒导入：包 __init__ 注册适配器，顶层导入成环
        timeseries: dict = {"engine": self.name, "run_name": req.run_name,
                            "bucket_seconds": 1.0,
                            "series": series, "combined": combined,
                            "errors": self._errors_summary(all_recs),
                            "summaries": serialize_summaries(out.rows)}
        if timeseries["series"]:
            ts_path = Path(cfg["output_dir"]).parent / "timeseries.json"
            ts_path.write_text(json.dumps(timeseries, ensure_ascii=False),
                               encoding="utf-8")
            out.report_files["timeseries.json"] = str(ts_path)
        out.report_files.update(glob_report_files(Path(cfg["output_dir"]).parent, (
            "locust_raw/**/*.html",
            "locust_raw/**/*_metrics.jsonl",
            "logs/locust.log",
        )))
        return out

    @staticmethod
    def _read_recs(path: Path) -> list[dict]:
        """读 metrics jsonl（跳过损坏行——截断/写盘竞争时宁缺毋错）。"""
        recs = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except ValueError:
                logger.warning("locust %s: 跳过损坏的指标行", path.name)
        return recs

    def _collect_level(self, req: ToolRunRequest, out: ToolRunResult,
                       recs: list[dict], label: str) -> None:
        """单档 metrics jsonl → 指标行（全部请求级样本聚合，宁缺毋错）。"""
        if not recs:
            logger.warning("locust %s: 无请求记录，跳过", label)
            return
        ok_recs = [r for r in recs if r.get("ok")]
        share = self._buffered_share(ok_recs)
        if share >= 0.9:
            logger.warning(
                "locust %s: %.0f%% 请求 TTFT≈总延迟——端点疑似整包缓冲（非真流式），"
                "TPOT/ITL 不可测拒绝入库（output_tps 墙钟聚合口径仍可测，保留）",
                label, share * 100)
            # 就地置空：指标行与秒级时序同步剔除假流式测量（TTFT 保留，退化为端到端口径）
            for r in recs:
                for k in ("tpot_ms", "itl_avg", "itl_p95", "gen_ms"):
                    r[k] = None
        total = len(recs)
        failed = total - len(ok_recs)

        def mean(key: str) -> float | None:
            vals = [float(r[key]) for r in ok_recs if r.get(key) is not None]
            return round(sum(vals) / len(vals), 1) if vals else None

        def p95(key: str) -> float | None:
            vals = sorted(float(r[key]) for r in ok_recs if r.get(key) is not None)
            if not vals:
                return None
            pos = min(int(0.95 * (len(vals) - 1)) + 1, len(vals) - 1)
            return vals[pos]

        span = self._span_seconds(recs)
        metrics = {
            "latency_avg_ms": mean("latency_ms"),
            "latency_p95_ms": p95("latency_ms"),
            "ttft_ms": mean("ttft_ms"),
            "ttft_p95_ms": p95("ttft_ms"),
            "tpot_ms": mean("tpot_ms"),
            "tpot_p95_ms": p95("tpot_ms"),
            "itl_ms": mean("itl_avg"),
            "itl_p95_ms": p95("itl_p95"),
            "qps": round(total / span, 3) if span and span > 0 else None,
            "error_rate": failed / total if total else None,
            "output_tps": self._global_tps(recs),
            "avg_input_tokens": self._mean_key(recs, "prompt_tokens"),
            "avg_output_tokens": self._mean_key(recs, "tokens"),
        }
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
        if metrics.get("qps") is not None:
            out.raw_score[label] = metrics["qps"]

    @staticmethod
    def _buffered_share(ok_recs: list[dict]) -> float:
        """TTFT≈端到端延迟的请求占比（网关缓冲/假流式检测）。

        整包缓冲的响应首字节随完整 body 一起到达（ttft_ms ≥ 0.95×latency_ms），
        ITL 全 0、TPOT 失真。真流式请求 TTFT 占比通常 <0.5，阈值留足余量。
        """
        if not ok_recs:
            return 0.0
        hits = sum(1 for r in ok_recs
                   if r.get("ttft_ms") is not None and r.get("latency_ms")
                   and float(r["ttft_ms"]) >= 0.95 * float(r["latency_ms"]))
        return hits / len(ok_recs)

    @staticmethod
    def _span_seconds(recs: list[dict]) -> float | None:
        """首末请求完成时刻差（秒）；不足两条返回 None。"""
        ts = sorted(float(r["t"]) for r in recs if r.get("t"))
        return (ts[-1] - ts[0]) if len(ts) >= 2 else None

    @staticmethod
    def _mean_key(recs: list[dict], key: str) -> float | None:
        vals = [float(r[key]) for r in recs if r.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    @staticmethod
    def _global_tps(recs: list[dict]) -> float | None:
        """输出吞吐（与 evalscope Output Throughput 同口径）：Σtokens ÷ 墙钟时长。

        evalscope 按测试墙钟聚合（8 并发的 tok/s 是 8 流之和）；locust 旧口径
        Σtokens/Σ生成秒实为单流速率（≈1000/ITL），同名指标差 ~在途并发倍——
        2026-09-17 对比工具首次暴露。单流速率由 ITL 已覆盖，此处对齐聚合口径。
        """
        with_tok = [r for r in recs if r.get("ok") and r.get("tokens")]
        if len(with_tok) < 2:
            return None
        total_tokens = sum(float(r["tokens"]) for r in with_tok)
        start = min(float(r["t"]) - float(r.get("latency_ms") or 0) / 1000.0
                    for r in with_tok)
        end = max(float(r["t"]) for r in with_tok)
        wall = end - start
        return round(total_tokens / wall, 1) if wall > 0 else None

    @staticmethod
    def _collect_failures(out: ToolRunResult, recs: list[dict], label: str) -> None:
        """错误明细聚合：错误文本 × 次数。"""
        errs: dict[str, int] = {}
        for rec in recs:
            if rec.get("ok") or not rec.get("error"):
                continue
            errs[rec["error"]] = errs.get(rec["error"], 0) + 1
        for err, n in sorted(errs.items(), key=lambda kv: -kv[1]):
            out.failures.append({"dataset": label, "case": "POST",
                                 "detail": f"{err} ×{n}"})

    def _series_from_recs(self, recs: list[dict]) -> tuple[list[dict], list[dict]]:
        """全部请求（带 users 标记）→ 各档 1s 桶 series + 全场 combined 阶梯。

        series 每档独立相对轴（t 自档内首秒起）；combined 复制各档点、按墙钟
        ts（各档进程同源时钟）排序、t 重定基为全场首秒——多档 ramp 一条
        连续曲线看完整个爬坡（与 evalscope-perf 的 combined 同语义）。
        """
        if not recs:
            return [], []
        by_user_sec: dict[tuple[int, int], dict] = {}
        for r in recs:
            t = r.get("t")
            if t is None:
                continue
            key = (int(r.get("users") or 0), int(t))
            b = by_user_sec.setdefault(key, {"n": 0, "fails": 0, "tokens": 0.0,
                                             "ttft": [], "latency": [], "tpot": [],
                                             "itl": []})
            b["n"] += 1
            b["tokens"] += float(r.get("tokens") or 0)
            if r.get("ok"):
                for dst, src in (("ttft", "ttft_ms"), ("latency", "latency_ms"),
                                 ("tpot", "tpot_ms"), ("itl", "itl_avg")):
                    v = r.get(src)
                    if v is not None:
                        b[dst].append(float(v))
            else:
                b["fails"] += 1
        t0_global = min(sec for _u, sec in by_user_sec)
        series: list[dict] = []
        combined: list[dict] = []
        for u in sorted({users for users, _sec in by_user_sec}):
            secs = {sec: b for (uu, sec), b in by_user_sec.items() if uu == u}
            t0 = min(secs)
            points = []
            for sec in sorted(secs):
                b = secs[sec]
                ttft_avg = round(sum(b["ttft"]) / len(b["ttft"]), 1) if b["ttft"] else None
                ttft_p95 = round(max(b["ttft"]), 1) if b["ttft"] else None
                lat_avg = round(sum(b["latency"]) / len(b["latency"]), 1) if b["latency"] else None
                lat_p95 = round(max(b["latency"]), 1) if b["latency"] else None
                tpot_avg = round(sum(b["tpot"]) / len(b["tpot"]), 1) if b["tpot"] else None
                tpot_p95 = round(max(b["tpot"]), 1) if b["tpot"] else None
                itl_avg = round(sum(b["itl"]) / len(b["itl"]), 1) if b["itl"] else None
                itl_p95 = round(max(b["itl"]), 1) if b["itl"] else None
                pt = {
                    "t": round(sec - t0, 3), "users": u, "ts": round(sec, 3),
                    "ttft_avg": ttft_avg, "ttft_p95": ttft_p95,
                    "latency_avg": lat_avg, "latency_p95": lat_p95,
                    "tpot_avg": tpot_avg, "tpot_p95": tpot_p95,
                    "itl_avg": itl_avg, "itl_p95": itl_p95,
                    "rps": round(b["n"], 3), "tok_s": round(b["tokens"], 1),
                    "errors": int(b["fails"]),
                }
                points.append(pt)
                combined.append({**pt, "t": round(sec - t0_global, 3)})
            series.append({"users": u, "run": 0, "label": f"locust:u{u}",
                           "points": points})
        combined.sort(key=lambda p: p["ts"])
        return series, combined

    def _errors_summary(self, recs: list[dict]) -> list[dict]:
        errs: dict[str, int] = {}
        for r in recs:
            if not r.get("ok") and r.get("error"):
                errs[r["error"]] = errs.get(r["error"], 0) + 1
        return [{"key": k, "count": c} for k, c in
                sorted(errs.items(), key=lambda kv: -kv[1])]

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


def _expand_ramp(ramp: dict) -> list[int]:
    """兼容旧导入名的别名（真实实现移至 perf_common.expand_ramp）。"""
    return expand_ramp(ramp)
