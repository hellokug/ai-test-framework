"""evalscope 适配器（模块 C：平台化首个工具接入）。

已验证（2026-09-01 本地微跑）：evalscope 1.9.1，`run_task(dict)` 进程内执行；
`eval_type: openai_api` + `api_url` 直连 OpenAI 兼容端点；返回
`{dataset: Report}`，Report.metrics=[Metric(name,num,score)]，另产
reports/*.json + HTML；eval_backend 可选 Native/OpenCompass/VLMEvalKit/RAGEval。

注意：`limit` 语义是**每科目**条数（ceval limit=3 实跑 52×3=156 题），
平台的 ``limit_per_subset`` 直接映射，文档必须标明。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from aite import config
from aite.tools.base import (
    ToolAdapter,
    ToolMetricRow,
    ToolRunRequest,
    ToolRunResult,
    api_endpoint,
    register_tool,
    run_sync_in_thread,
)
from aite.tools.subproc import glob_report_files, kill_tree
from aite.utils.logging import get_logger

logger = get_logger(__name__)

# metric_thresholds: auto 时的推荐阈值（选择题类 = 随机基线；开放题不设默认）。
# 只列有客观随机基线的选择题基准，避免给开放生成题一个会误导的门槛。
RECOMMENDED_THRESHOLDS: dict[str, dict[str, float]] = {
    "mmlu": {"mean_acc": 0.25},       # 4 选 1
    "ceval": {"mean_acc": 0.25},      # 4 选 1
    "cmmlu": {"mean_acc": 0.25},      # 4 选 1
    "arc": {"mean_acc": 0.25},        # 4 选 1
    "hellaswag": {"mean_acc": 0.25},  # 4 选 1
    "winogrande": {"mean_acc": 0.5},  # 2 选 1
    "truthful_qa": {"mean_acc": 0.25},
    # gsm8k/math 等开放生成题：无客观随机基线，不给默认（避免误导）
}


def _normalize_metric_names(metric_list: list) -> list[str]:
    """evalscope metric_list → 报告可见的指标名列表。

    metric_list 两种形态：``['acc']`` 或 ``[{'acc': {'numeric': True}}]``；
    evalscope 内部名 ``acc``（accuracy）在报告/阈值中显示为 ``mean_acc``。
    """
    names: list[str] = []
    for m in metric_list or []:
        if isinstance(m, str):
            names.append(m)
        elif isinstance(m, dict):
            names.extend(m.keys())
    return ["mean_acc" if n == "acc" else n for n in names]


def _threshold_aliases(name: str) -> tuple[str, ...]:
    """阈值键容错序列：各基准报告指标名不一（gsm8k 报 ``mean_acc``，有的基准
    报原始名 ``acc``，schema v2 报告报 ``accuracy``），suite 阈值按任一写法
    都不该静默失效成「不判定」。"""
    return {"acc": ("acc", "mean_acc"),
            "mean_acc": ("mean_acc", "acc"),
            "accuracy": ("accuracy", "mean_acc", "acc")}.get(name, (name,))


def _metric_name(m: dict) -> str:
    """报告指标行取名：schema v2 把名字放在 identity.name（如 ``accuracy``），
    旧版平铺在 name；聚合基准无名行兜底 ``score``。只读旧字段时 v2 报告
    指标全部记成 score，auto 阈值与显式阈值别名都会静默失效。"""
    ident = m.get("identity")
    return str((ident or {}).get("name") or m.get("name") or "score")


_PROGRESS_RE = re.compile(
    r"(Evaluating\[[^\]]*\]|Running\[[^\]]*\])\s+\d+%\|\s*(\d+)/(\d+)")
_PROGRESS_RE_PULL = re.compile(
    r"Pulling SWE-Bench images\s+\d+%\|\s*(\d+)/(\d+)")

# 阶段行（无 done/total，仅状态播报——运行期"现在在干嘛"可见性）
_STAGE_PATTERNS = (
    (re.compile(r"Start loading benchmark dataset"), "加载数据集"),
    (re.compile(r"Attempting to pull (\d+) SWE-Bench images", re.I), r"拉取实例镜像 ×\1"),
    (re.compile(r"Base images built successfully"), "基础镜像就绪"),
    (re.compile(r"No environment images need to be built"), "环境镜像就绪"),
    (re.compile(r"Building instance images for (\d+) instances"), r"构建实例镜像 ×\1"),
    (re.compile(r"SandboxService: manager started"), "沙箱已启动"),
)


def parse_progress(line: str) -> dict | None:
    """evalscope 日志行 → 进度/阶段事件（与原生引擎 progress 事件同形）。

    计数行（Evaluating/Running/拉取进度）带 done/total；阶段行仅 phase。"""
    m = _PROGRESS_RE.search(line)
    if m:
        return {"type": "progress", "phase": m.group(1),
                "done": int(m.group(2)), "total": int(m.group(3))}
    m = _PROGRESS_RE_PULL.search(line)
    if m:
        return {"type": "progress", "phase": "拉取实例镜像",
                "done": int(m.group(1)), "total": int(m.group(2))}
    for pat, text in _STAGE_PATTERNS:
        m = pat.search(line)
        if m:
            return {"type": "progress",
                    "phase": m.expand(text) if m.groups() else text}
    return None


def _tasks_from_stdout(stdout: str) -> list[dict]:
    """bootstrap stdout → 任务列表；evalscope import 期 banner 可能混入，
    从末行倒序取首个可解析的 JSON 数组（bootstrap 的 print 在最后）。"""
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, list):
            return data
    raise RuntimeError("benchmarks 列举无 JSON 输出")


def _dispatch_line(line: str, cb) -> None:
    """evalscope 日志行分流：命中进度模式 → 进度事件（驱动进度条）；
    其余原始行 → log 镜像事件（控制台可见性——跑着的时候后台在干嘛不用猜）。
    截断 300 字符防 tqdm 长行刷屏；空行丢弃。"""
    ev = parse_progress(line)
    if ev:
        cb(ev)
        return
    trimmed = line.strip()
    if trimmed:
        cb({"type": "log", "line": trimmed[:300]})


def _is_identity_conflict(tail: str) -> bool:
    """evalscope 续推身份校验失败：上次目录无快照（中断早于落盘）或异版本写入。"""
    return "Cached evaluation identity does not match" in tail


@register_tool("evalscope")
class EvalscopeAdapter(ToolAdapter):
    name = "evalscope"

    # 适配器保留键（tools.extra_args 内）：不透传给 TaskConfig
    RESERVED_KEYS = ("python_exe", "timeout_s")

    # 独立环境子进程引导：配置经文件契约传入（零 CLI 旗标漂移风险），
    # 报告由 collect() 按既有 reports/*.json 契约回收（原则 12）
    RUN_TASK_BOOTSTRAP = """
import json, pathlib, sys

_orig_write_text = pathlib.Path.write_text

def _lf_write_text(self, data, *args, **kwargs):
    kwargs.setdefault("newline", "\\n")
    return _orig_write_text(self, data, *args, **kwargs)

pathlib.Path.write_text = _lf_write_text   # Windows 宿主 CRLF 毒化容器内 eval.sh/补丁

from evalscope import run_task
from evalscope.config import TaskConfig
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
run_task(TaskConfig(**cfg))
"""

    # 独立环境列举基准：主环境未装 evalscope（仅 .venv-evalscope/conda env 有）
    # 时 `aite tool benchmarks` 的回退路径。字段抽取与 list_tasks 同口径；结果
    # 走 stdout 末行 JSON（evalscope import 期可能在 stdout 打 banner，取末行
    # 可解析的 JSON 数组最稳）。独立环境无 aite 包，归一化逻辑就地内联。
    LIST_TASKS_BOOTSTRAP = """
import json

from evalscope.api.registry import BENCHMARK_REGISTRY


def _norm(metric_list):
    names = []
    for m in metric_list or []:
        if isinstance(m, str):
            names.append(m)
        elif isinstance(m, dict):
            names.extend(m.keys())
    return ["mean_acc" if n == "acc" else n for n in names]


out = []
for name, meta in sorted(BENCHMARK_REGISTRY.items()):
    out.append({
        "name": name,
        "pretty_name": meta.pretty_name or name,
        "metrics": _norm(meta.metric_list),
        "tags": list(meta.tags or []),
        "few_shot_num": meta.few_shot_num,
        "subsets": list(meta.subset_list or []),
        "dataset_id": meta.dataset_id,
    })
print(json.dumps(out, ensure_ascii=False))
"""

    @classmethod
    def list_tasks_via(cls, python_exe: str) -> list[dict]:
        """经独立环境解释器列举基准（环境注入与 run 子进程同款）。"""
        env = {**os.environ, "PYTHONUTF8": "1"}
        proc = subprocess.run(
            [python_exe, "-c", cls.LIST_TASKS_BOOTSTRAP],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=300, env=env)
        if proc.returncode != 0:
            raise RuntimeError(
                f"benchmarks 列举失败（{python_exe}）：{(proc.stderr or '')[-400:]}")
        return _tasks_from_stdout(proc.stdout)

    def build_config(self, req: ToolRunRequest) -> dict:
        url, api_key = api_endpoint(req.target)
        cfg: dict[str, Any] = {
            "model": req.target.get("model"),
            "api_url": url,
            "api_key": api_key,
            "eval_type": "openai_api",
            "datasets": list(req.datasets),
            "work_dir": str(req.work_dir),
            "stream": bool(req.target.get("stream", False)),
            "no_timestamp": True,     # 输出直接进 work_dir；时间戳由 aite 侧目录承担
        }
        if req.limit_per_subset:
            cfg["limit"] = req.limit_per_subset
        if req.resume:
            # 断点续推：evalscope 会就地续推该目录（work_dir = use_cache），
            # 按 sample_id 复用已完成预测/判分；改判官后想只复用预测重判，
            # 另传 extra_args.rerun_review: true
            cfg["use_cache"] = str(req.work_dir)
        extra = {k: v for k, v in (req.extra or {}).items()
                 if k not in self.RESERVED_KEYS}
        if req.judge:
            self._apply_judge(cfg, req.judge, extra)
        cfg.update(extra)             # eval_backend / generation_config 等原生参数透传
        return cfg

    @staticmethod
    def _apply_judge(cfg: dict, judge: dict, extra: dict) -> None:
        """tools.judge 预制（已展开为 target 同形态）→ evalscope 原生 typed judge。

        新版 evalscope 对 LLM 判官类基准强制要求显式 judge.models（不配起跑
        即拒，旧的「被测模型自判」缺省已移除）；这里映射成 TaskConfig 原生
        ``judge.models[]`` 结构，语义校验交给 evalscope 侧。"""
        if "judge" in extra:
            raise ValueError(
                "tools.judge（预制引用）与 tools.extra_args.judge（原生透传）同时配置——二选一")
        if not judge.get("model") or not judge.get("base_url"):
            raise ValueError(
                "tools.judge 须含 base_url 与 model（{profile: <common 预制>} 引用，"
                "或内联 {base_url, model, api_key_env}）")
        url, api_key = api_endpoint(judge)
        model: dict[str, Any] = {
            "model_id": judge["model"], "api_url": url, "api_key": api_key}
        if isinstance(judge.get("params"), dict):
            model["generation_config"] = judge["params"]
        cfg["judge"] = {"models": [model]}

    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        python_exe = (req.extra or {}).get("python_exe")
        if python_exe:
            # agent 级长任务 + evalscope 独立环境（如 py3.10 venv）：子进程 + 文件契约
            return await self._run_subprocess(req, str(python_exe))
        try:
            from evalscope import run_task  # 进程内依赖；装在统一 conda 环境
        except ImportError as exc:
            raise RuntimeError(
                "evalscope 不可导入：安装到 aite 环境，或在 tools.extra_args."
                f"python_exe 指向独立 evalscope 环境（原错误：{exc}）") from exc
        cfg = self.build_config(req)
        await run_sync_in_thread(run_task, cfg)
        return self.collect(req)

    async def _run_subprocess(self, req: ToolRunRequest, python_exe: str
                              ) -> ToolRunResult:
        try:
            await self._run_subprocess_impl(req, python_exe)
        except RuntimeError as exc:
            if not (req.resume and _is_identity_conflict(str(exc))):
                raise
            # evalscope 设计的显式覆盖路径：上次目录无有效身份快照（中断早于
            # 快照落盘/异版本写入）→ 复用已完成预测、重算判分。判分调用会重
            # 花，但预测（生成成本大头）保留；仅自动兜底一次，不循环。
            notice = ("缓存身份校验不符（上次目录无有效快照）→ 自动 rerun_review："
                      "复用已完成预测、重算判分")
            logger.warning(notice)
            if req.progress_cb:
                req.progress_cb({"type": "log", "line": notice})
            req.extra = {**req.extra, "rerun_review": True}
            await self._run_subprocess_impl(req, python_exe)
        return self.collect(req)

    @staticmethod
    def _normalize_cfg_paths(cfg: dict, work_dir: Path) -> None:
        """子进程 cwd 切进 work_dir，配置里的路径字段一律改写绝对路径——相对
        路径会在切换后的 cwd 下二次拼接。use_cache 漏改的实弹教训（2026-09-29）：
        续推产物整套嵌套进 work_dir/reports/tool/.../，collect 靠 glob 兜住但
        产物错位、报告路径翻倍。"""
        cfg["work_dir"] = str(work_dir)
        if cfg.get("use_cache"):
            cfg["use_cache"] = str(work_dir)

    async def _run_subprocess_impl(self, req: ToolRunRequest, python_exe: str
                                   ) -> None:
        work_dir = req.work_dir.resolve()
        cfg = self.build_config(req)
        self._normalize_cfg_paths(cfg, work_dir)
        timeout_s = float((req.extra or {}).get("timeout_s")
                          or config.DEFAULT_EVALSCOPE_TASK_TIMEOUT_S)
        cfg_path = work_dir / "task_cfg.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        # 中文 Windows 保命：docker-py/工具链读 UTF-8 文件（如 docker context
        # meta 含用户名路径）默认用 GBK 解码会炸，UTF-8 模式一揽子纠正；
        # HF 镜像默认注入（huggingface.co 直连不可达的环境开箱即用）
        env = {**os.environ, "PYTHONUTF8": "1"}
        env.setdefault("HF_ENDPOINT", config.DEFAULT_HF_ENDPOINT)
        log_path = work_dir / "logs" / "eval_log.log"   # evalscope 自写日志
        stdout_path = work_dir / "proc_stdout.log"
        stderr_path = work_dir / "proc_stderr.log"
        with stdout_path.open("wb") as out_fh, stderr_path.open("wb") as err_fh:
            # 句柄仅 spawn 需要（子进程自行持有副本），随后即可关闭
            proc = subprocess.Popen(
                [python_exe, "-c", self.RUN_TASK_BOOTSTRAP, str(cfg_path)],
                cwd=str(work_dir), env=env, stdout=out_fh, stderr=err_fh)
        deadline = asyncio.get_event_loop().time() + timeout_s
        offset = 0
        try:
            while proc.poll() is None:
                if asyncio.get_event_loop().time() > deadline:
                    kill_tree(proc)
                    raise RuntimeError(
                        f"evalscope task timed out after {timeout_s}s — "
                        "可用 tools.extra_args.timeout_s 调大")
                if req.progress_cb and log_path.exists():
                    with log_path.open(encoding="utf-8", errors="replace") as fh:
                        fh.seek(offset)
                        new_text = fh.read()
                        offset = fh.tell()
                    for raw_line in new_text.splitlines():
                        _dispatch_line(raw_line, req.progress_cb)
                await asyncio.sleep(3)
        except BaseException:
            kill_tree(proc)
            raise
        if proc.returncode != 0:
            tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-800:] \
                or stdout_path.read_text(encoding="utf-8", errors="replace")[-800:]
            raise RuntimeError(f"evalscope task failed: {tail}")

    def list_tasks(self) -> list[dict]:
        """枚举 evalscope 全部基准：name/metrics/tags/few_shot_num/subsets。"""
        from evalscope.api.registry import BENCHMARK_REGISTRY  # 进程内依赖

        out: list[dict] = []
        for name, meta in sorted(BENCHMARK_REGISTRY.items()):
            out.append({
                "name": name,
                "pretty_name": meta.pretty_name or name,
                "metrics": _normalize_metric_names(meta.metric_list),
                "tags": list(meta.tags or []),
                "few_shot_num": meta.few_shot_num,
                "subsets": list(meta.subset_list or []),
                "dataset_id": meta.dataset_id,
            })
        return out

    def collect(self, req: ToolRunRequest) -> ToolRunResult:
        """从 work_dir 的 reports/*.json 收集（稳定文件契约，不依赖内存返回对象）。

        文件结构（已实测验证）：dataset_name / score / metrics[{name,num,score}] /
        perf_metrics.summary{n_samples,...}。
        """
        import json as _json

        out = ToolRunResult()
        seen: set[str] = set()
        # 布局两种都兼容：reports/<model>@<ds>.json（带时间戳模式）与
        # reports/<model>/<ds>.json（no_timestamp 模式）
        for p in sorted(req.work_dir.glob("**/reports/**/*.json")):
            try:
                data = _json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            ds_name = str(data.get("dataset_name") or p.stem.split("@")[-1])
            if ds_name in seen:
                continue
            seen.add(ds_name)
            label = f"evalscope:{ds_name}"
            if data.get("score") is not None:
                out.raw_score[ds_name] = float(data["score"])
            metrics = [m for m in data.get("metrics") or [] if m.get("score") is not None]
            # 聚合基准（如 swe_bench agentic）metrics 可能无名且缺顶层 score——
            # 单指标时以 "score" 兜底命名并回填 raw_score
            if data.get("score") is None and len(metrics) == 1:
                out.raw_score[ds_name] = float(metrics[0]["score"])
            for m in metrics:
                name = _metric_name(m)
                thr = next((req.metric_thresholds[a]
                            for a in _threshold_aliases(name)
                            if req.metric_thresholds.get(a) is not None), None)
                rec = (RECOMMENDED_THRESHOLDS.get(ds_name) or {}
                       ) if (thr is None and req.auto_thresholds) else {}
                if thr is None:
                    thr = next((rec[a] for a in _threshold_aliases(name)
                                if rec.get(a) is not None), None)
                value = float(m["score"])
                out.rows.append(ToolMetricRow(
                    dataset=label, metric=name, value=value,
                    count=int(m.get("num") or 0), threshold=thr,
                    passed=(value >= thr) if thr is not None else None,
                ))
            perf = data.get("perf_metrics") or {}
            summary = perf.get("summary") if isinstance(perf, dict) else None
            if summary:
                out.perf[ds_name] = summary
        files = glob_report_files(req.work_dir,
                                  ("**/reports/**/*.json", "**/reports/**/*.html"))
        for name, path in files.items():
            out.report_files.setdefault(name, path)
        return out
