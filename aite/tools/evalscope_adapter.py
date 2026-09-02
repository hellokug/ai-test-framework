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
        for pattern in ("**/reports/**/*.json", "**/reports/**/*.html"):
            for p in sorted(req.work_dir.glob(pattern)):
                out.report_files.setdefault(p.name, str(p))
        return out
