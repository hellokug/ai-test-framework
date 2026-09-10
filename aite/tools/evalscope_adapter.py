"""evalscope 适配器（模块 C：平台化首个工具接入）。

已验证（2026-09-01 本地微跑）：evalscope 1.9.1，`run_task(dict)` 进程内执行；
`eval_type: openai_api` + `api_url` 直连 OpenAI 兼容端点；返回
`{dataset: Report}`，Report.metrics=[Metric(name,num,score)]，另产
reports/*.json + HTML；eval_backend 可选 Native/OpenCompass/VLMEvalKit/RAGEval。

注意：`limit` 语义是**每科目**条数（ceval limit=3 实跑 52×3=156 题），
平台的 ``limit_per_subset`` 直接映射，文档必须标明。
"""

from __future__ import annotations

from typing import Any

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


@register_tool("evalscope")
class EvalscopeAdapter(ToolAdapter):
    name = "evalscope"

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
        cfg.update(req.extra or {})   # eval_backend / generation_config 等原生参数透传
        return cfg

    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        from evalscope import run_task  # 进程内依赖；装在统一 conda 环境

        cfg = self.build_config(req)
        await run_sync_in_thread(run_task, cfg)
        return self.collect(req)

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
            for m in data.get("metrics") or []:
                thr = req.metric_thresholds.get(m.get("name"))
                if thr is None and req.auto_thresholds:
                    thr = (RECOMMENDED_THRESHOLDS.get(ds_name) or {}).get(m.get("name"))
                value = m.get("score")
                if value is None:
                    continue
                out.rows.append(ToolMetricRow(
                    dataset=label, metric=str(m.get("name")), value=float(value),
                    count=int(m.get("num") or 0), threshold=thr,
                    passed=(float(value) >= thr) if thr is not None else None,
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
