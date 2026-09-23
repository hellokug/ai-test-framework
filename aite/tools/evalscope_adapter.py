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
from aite.tools.subproc import glob_report_files

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
        extra = {k: v for k, v in (req.extra or {}).items()
                 if k not in self.RESERVED_KEYS}
        cfg.update(extra)             # eval_backend / generation_config 等原生参数透传
        return cfg

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
        await self._run_subprocess_impl(req, python_exe)
        return self.collect(req)

    async def _run_subprocess_impl(self, req: ToolRunRequest, python_exe: str
                                   ) -> None:
        # 子进程 cwd 切进 work_dir，配置与产物一律绝对路径（相对路径会在
        # 切换后的 cwd 下二次拼接——首跑实弹教训）
        work_dir = req.work_dir.resolve()
        cfg = self.build_config(req)
        cfg["work_dir"] = str(work_dir)
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
                    proc.kill()
                    raise RuntimeError(
                        f"evalscope task timed out after {timeout_s}s — "
                        "可用 tools.extra_args.timeout_s 调大")
                if req.progress_cb and log_path.exists():
                    with log_path.open(encoding="utf-8", errors="replace") as fh:
                        fh.seek(offset)
                        new_text = fh.read()
                        offset = fh.tell()
                    for line in new_text.splitlines():
                        ev = parse_progress(line)
                        if ev:
                            req.progress_cb(ev)
                await asyncio.sleep(3)
        except BaseException:
            proc.kill()
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
                name = str(m.get("name") or "score")
                thr = req.metric_thresholds.get(name)
                if thr is None and req.auto_thresholds:
                    thr = (RECOMMENDED_THRESHOLDS.get(ds_name) or {}).get(name)
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
