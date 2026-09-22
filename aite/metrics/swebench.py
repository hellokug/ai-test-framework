"""SWE-bench resolve 判定指标：官方 harness 子进程打分（原则 12：CLI 子进程 + 文件契约）。

每个 case：把补丁写成单行 predictions JSONL → 调
``python -m swebench.harness.run_evaluation --instance_ids <id> --run_id <id>``
（子进程 CWD 切到 case 工作目录，harness 的相对 logs/ 不污染仓库根）→ 回读
``<report_dir>/<model>.<run_id>.json``（schema v2）判定 resolved。

依赖：``pip install swebench`` + 运行中的 Docker（评测镜像仅 Linux x86_64，
Windows 走 WSL2）；国内网络设 ``HF_ENDPOINT=https://hf-mirror.com``（harness
运行时同样经 HF 拉数据集）。值语义：

- 1.0 resolved / 0.0 未解或空补丁（无补丁必不 resolve，省分钟级容器开销）；
- harness 失败、基础设施错误、实例缺席报告 → value=None（宁缺毋错）。

params：dataset_name / model（报告文件名与预测记录 model_name_or_path）/
timeout_s（单实例墙钟上限，含镜像拉取）/ work_dir / python_exe（harness 解释器，
默认当前进程解释器——打分环境可与 aite 环境解耦）。
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from aite.config import (
    DEFAULT_SWE_RESOLVE_TIMEOUT_S,
    DEFAULT_SWEBENCH_DATASET,
    DEFAULT_SWEBENCH_WORK_DIR,
)
from aite.core.interfaces import Metric
from aite.core.registry import register_metric
from aite.core.types import MetricResult

if TYPE_CHECKING:
    from aite.core.types import EvalContext


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", text)[:80] or "case"


# Windows 宿主 workaround：swebench v5.0.2 run_evaluation.py 以 write_text 落
# eval.sh——Windows 文本模式 LF→CRLF，脚本进 Linux 容器后 git apply / pytest 被
# 行尾 \r 毒倒（patch does not apply / command not found）。上游仅支持 Linux/macOS
# 宿主；此处只对打分子进程内 Path.write_text 强制 LF，经 runpy 走 -m 同构入口。
_HARNESS_BOOTSTRAP = """
import pathlib, runpy

_orig = pathlib.Path.write_text

def _lf_write_text(self, data, *args, **kwargs):
    kwargs.setdefault("newline", "\\n")
    return _orig(self, data, *args, **kwargs)

pathlib.Path.write_text = _lf_write_text
runpy.run_module("swebench.harness.run_evaluation", run_name="__main__", alter_sys=True)
"""


def _run_harness(pred_file: Path, *, instance_id: str, dataset_name: str,
                 run_id: str, report_dir: Path, model: str, python_exe: str,
                 timeout_s: float) -> dict[str, Any]:
    """同步起 harness 子进程，回读 report JSON；任何失败抛 RuntimeError。"""
    report_dir.mkdir(parents=True, exist_ok=True)
    cmd = [python_exe, "-c", _HARNESS_BOOTSTRAP,
           "--predictions_path", str(pred_file),
           "--dataset_name", dataset_name,
           "--instance_ids", instance_id,
           "--run_id", run_id,
           "--report_dir", str(report_dir),
           "--max_workers", "1"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s, cwd=str(report_dir.parent))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"swebench harness timed out after {timeout_s}s (instance {instance_id})") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-800:]
        hint = ("swebench not installed in harness env (pip install swebench)"
                if "No module named swebench" in tail else "see harness stderr")
        raise RuntimeError(f"swebench harness failed: {hint}: {tail}")
    report_file = report_dir / f"{model.replace('/', '__')}.{run_id}.json"
    if not report_file.exists():
        raise RuntimeError(f"harness report missing: {report_file}")
    return json.loads(report_file.read_text(encoding="utf-8"))


def _resolve_from_report(rep: dict[str, Any],
                         instance_id: str) -> tuple[float | None, str | None]:
    """schema v2 report → (value, error)：resolved=1.0；未解/空补丁=0.0；
    错误/基础设施失败/缺席报告=None（打分本身失败不等于未解）。"""
    if instance_id in rep.get("resolved_ids", []):
        return 1.0, None
    if instance_id in rep.get("unresolved_ids", []) \
            or instance_id in rep.get("empty_patch_ids", []):
        return 0.0, None
    if instance_id in rep.get("infra_failure_ids", []):
        reason = rep.get("failure_reasons", {}).get(instance_id, "environment failure")
        return None, f"infra failure: {reason}"
    if instance_id in rep.get("error_ids", []):
        return None, "harness error on instance"
    return None, "instance absent from harness report"


@register_metric("swe_resolution")
class SweResolution(Metric):
    """单实例补丁解析判定；均值聚合即 resolve rate（需本机 Docker + swebench）。"""

    name: ClassVar[str] = "swe_resolution"
    aggregation: ClassVar[str] = "mean"

    async def compute(self, *, case, response, responses=None,
                      ctx: EvalContext) -> MetricResult:
        instance_id = str(case.metadata.get("instance_id") or case.id)
        run_id = f"aite-{_slug(str(ctx.run_id or ctx.run_name or 'adhoc'))}-{_slug(instance_id)}"
        if not response.ok:
            return MetricResult(metric=self.name, value=None,
                                error=f"target failed: {response.error}")
        patch = response.output_text
        if not patch.strip():
            return MetricResult(metric=self.name, value=0.0,
                                raw={"reason": "empty_patch", "instance_id": instance_id})
        params = self.params
        model = str(params.get("model") or "aite")
        work_dir = (Path(str(params.get("work_dir") or DEFAULT_SWEBENCH_WORK_DIR))
                    / run_id).resolve()
        pred_file = work_dir / "predictions.jsonl"
        work_dir.mkdir(parents=True, exist_ok=True)
        pred_file.write_text(json.dumps({
            "instance_id": instance_id,
            "model_name_or_path": model,
            "model_patch": patch,
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        try:
            rep = await asyncio.to_thread(
                _run_harness, pred_file,
                instance_id=instance_id,
                dataset_name=str(params.get("dataset_name") or DEFAULT_SWEBENCH_DATASET),
                run_id=run_id,
                report_dir=work_dir / "reports",
                model=model,
                python_exe=str(params.get("python_exe") or sys.executable),
                timeout_s=float(params.get("timeout_s") or DEFAULT_SWE_RESOLVE_TIMEOUT_S),
            )
            value, err = _resolve_from_report(rep, instance_id)
        except (RuntimeError, OSError, ValueError) as exc:
            return MetricResult(metric=self.name, value=None, error=str(exc))
        return MetricResult(metric=self.name, value=value, error=err,
                            raw={"instance_id": instance_id, "run_id": run_id})
