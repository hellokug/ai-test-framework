"""evalscope-rageval 适配器（存量自研指标迁移第一项：RAGAS 五件套回归开源）。

委托 evalscope RAGEval 后端的官方 ragas 集成（``pip install 'evalscope[rag]'``，
ragas>=0.4 官方指标实现），aite 不写任何指标算法——本适配器只做三件胶水事：
字段映射（TestCase → ragas testset）、客户端注入（critic_llm/embeddings 翻译成
evalscope 配置）、分数归一（``*_score.json`` → 平台指标行）。

两种编排（B 是 A 跳过调模型这步的退化形态）：
- A（配 target，数据集 records 无 response）：逐 case 调被测端点生成答案后打分
- B（records 自带 response 字段，如 ai_study 等 RAG 系统的现成输出）：直接打分

suite 形态::

    engine: evalscope-rageval
    target: {…}                      # 方案 A
    tools:
      datasets: [./data/.../rgb_openbook.jsonl]   # 本地路径或 builtin id
      metric_thresholds: {faithfulness: 0.6}
    rageval:
      metrics: [faithfulness, context_precision, context_recall]
      judge: {base_url: …, model: …, api_key_env: …}
      embeddings: {model_name_or_path: BAAI/bge-large-zh-v1.5}   # relevancy/correctness 需要
      language: chinese
"""

from __future__ import annotations

import contextlib
import json
import math
from typing import Any

from aite.tools.base import (
    ToolAdapter,
    ToolMetricRow,
    ToolRunRequest,
    ToolRunResult,
    register_tool,
    run_sync_in_thread,
)

# 开源 ragas 官方指标名（= ragas.metrics 属性名，evalscope import_metric 按名解析）
RAGAS_METRICS = ["faithfulness", "answer_relevancy", "context_precision",
                 "context_recall", "answer_correctness"]
# 需要 embedding 客户端的指标（缺 embeddings 配置时 fail-fast）
_NEEDS_EMBEDDINGS = {"answer_relevancy", "answer_correctness"}


def _resolve_api_key(cfg: dict) -> str | None:
    if cfg.get("api_key"):
        return str(cfg["api_key"])
    env = cfg.get("api_key_env")
    from aite.utils.secrets import resolve_secret

    return resolve_secret(env) if env else None


def _patch_ragas_instructor_model_args(judge_cfg: dict) -> None:
    """ragas instructor 适配器 create_llm 硬编码 model_args=InstructorModelArgs()
    （缺省 max_tokens=1024）——llm_factory 的 kwargs 与 evalscope 配置的
    max_tokens/temperature 均不会生效，判分 JSON 输出超限即 IncompleteOutputException
    截断（2026-09-24/27 实测，中文长文档必现）。子类化注入配置值（缺省 8192 =
    DeepSeek 系 API 输出封顶）；ragas 上游修复后自动闲置。"""
    import ragas.llms.adapters.instructor as instructor_adapter

    max_tokens = int(judge_cfg.get("max_tokens", 8192))
    temperature = float(judge_cfg.get("temperature", 0.01))
    real_args = instructor_adapter.InstructorModelArgs

    class _PatchedModelArgs(real_args):
        def __init__(self, **kwargs):
            kwargs.setdefault("max_tokens", max_tokens)
            kwargs.setdefault("temperature", temperature)
            super().__init__(**kwargs)

    instructor_adapter.InstructorModelArgs = _PatchedModelArgs


def _patch_ragas_langchain_compat() -> None:
    """ragas 0.4.3 顶层硬 import langchain_community.chat_models.vertexai，
    langchain-community>=0.4 已移除该模块（ragas 侧兼容缺口，2026-09-23 实测）。

    仅 vertexai provider 会真正用到该实现——provider=openai（我们的全部场景）
    不受影响。缺失时注入惰性占位模块补齐 import 面；ragas 上游修复后自动闲置。
    """
    import importlib
    import sys
    import types

    try:
        importlib.import_module("langchain_community.chat_models.vertexai")
        return
    except ModuleNotFoundError:
        pass
    stub = types.ModuleType("langchain_community.chat_models.vertexai")
    stub.ChatVertexAI = type("ChatVertexAI", (), {})
    sys.modules[stub.__name__] = stub
    try:
        import langchain_community.chat_models as _chat_models

        _chat_models.vertexai = stub
    except ModuleNotFoundError:  # langchain_community 整体缺失：交由 evalscope 导入报错
        pass


@register_tool("evalscope-rageval")
class RagevalAdapter(ToolAdapter):
    """RAGAS 五件套打分（方案 A 调被测端点出答案 / 方案 B 引用既有 response）。"""

    def list_tasks(self) -> list[dict]:
        return [{"name": m, "metrics": [m], "tags": ["ragas", "rag"]}
                for m in RAGAS_METRICS]

    # ------------------------------------------------------------- 数据装载
    async def _load_cases(self, req: ToolRunRequest) -> list:
        """tools.datasets = 本地 jsonl/json 路径 或 builtin id → TestCase 列表。"""
        from aite.core.loader import DatasetConfig
        from aite.datasets.builtin import BuiltinDataset
        from aite.datasets.local import LocalFileDataset

        cases = []
        for ds in req.datasets:
            if ds.lower().endswith((".jsonl", ".json", ".csv")):
                cfg = DatasetConfig(type="local", path=ds,
                                    limit=req.limit_per_subset)
                cases += [c async for c in LocalFileDataset().load(cfg)]
            else:
                cfg = DatasetConfig(type="builtin", id=ds,
                                    limit=req.limit_per_subset)
                cases += [c async for c in BuiltinDataset().load(cfg)]
        if not cases:
            raise ValueError("rageval：数据集为空（tools.datasets 指向的文件无记录）")
        return cases

    @staticmethod
    def _split_modes(cases: list) -> tuple[list, list]:
        """B 模式 case（metadata 带 response 的现成答案）/ A 模式 case（需调端点）。"""
        with_resp = [c for c in cases if c.metadata.get("response")]
        need_invoke = [c for c in cases if not c.metadata.get("response")]
        if with_resp and need_invoke:
            missing = [c.id for c in need_invoke[:5]]
            raise ValueError(
                "rageval：数据集 response 字段部分缺失（混合形态不支持）——"
                f"缺失 case 如：{missing}；配 target 走方案 A，或补齐 response 走方案 B")
        return need_invoke, with_resp

    @staticmethod
    def _testset_record(case, response_text: str) -> dict:
        return {
            "user_input": str(case.input),
            "response": response_text,
            "retrieved_contexts": list(case.retrieval_context or []),
            "reference": "" if case.expected is None else str(case.expected),
        }

    async def _invoke_answers(self, req: ToolRunRequest, cases: list) -> dict[str, str]:
        """方案 A：逐 case 调被测端点生成答案（复用平台 connector，不重写调用）。"""
        from aite.core.loader import TargetConfig
        from aite.core.registry import resolve_connector
        from aite.core.types import InvokeOptions

        tcfg = TargetConfig(**(req.target or {}))
        connector = resolve_connector(tcfg.type or "openai_compatible")(tcfg)
        answers: dict[str, str] = {}
        try:
            for i, case in enumerate(cases, 1):
                resp = await connector.invoke(case, InvokeOptions())
                if not resp.ok:
                    raise RuntimeError(
                        f"rageval 方案 A：case {case.id} 调用失败：{resp.error}")
                answers[case.id] = resp.output_text
                req.progress_cb and req.progress_cb(
                    {"phase": "generate", "done": i, "total": len(cases),
                     "note": f"{case.id}"})
        finally:
            await connector.close()
        return answers

    # ------------------------------------------------------------- 实现契约
    def build_config(self, req: ToolRunRequest) -> dict:
        """平台配置 → evalscope run_task 配置（纯函数；testset 落盘在 run 阶段）。"""
        cfg = req.rag_eval or {}
        judge = cfg.get("judge") or {}
        if not judge.get("model"):
            raise ValueError("rageval.judge.model 必配（判官模型，如 deepseek-flash）")
        metrics = cfg.get("metrics") or ["faithfulness", "context_precision",
                                         "context_recall"]
        unknown = [m for m in metrics if m not in RAGAS_METRICS]
        if unknown:
            raise ValueError(f"rageval.metrics 含未知指标 {unknown}（可选：{RAGAS_METRICS}）")
        embeddings = cfg.get("embeddings")
        if not embeddings or not embeddings.get("model_name_or_path"):
            # evalscope RAGASEvalConfig 的 embeddings 为必填字段（2026-09-24 实测
            # ValidationError），即使只跑纯 LLM 指标也必须提供——本地小模型即可
            raise ValueError(
                "rageval.embeddings.model_name_or_path 必配（evalscope RAGEval 必填字段，"
                "如 {model_name_or_path: BAAI/bge-large-zh-v1.5}；answer_relevancy/"
                "answer_correctness 指标本身也依赖它）")

        api_key = _resolve_api_key(judge)
        if api_key is None:
            if judge.get("api_key_env"):
                raise ValueError(
                    f"judge.api_key_env 指向的环境变量 {judge['api_key_env']} 未设置")
            api_base = judge.get("base_url") or ""
            if "localhost" in api_base or "127.0.0.1" in api_base:
                api_key = "EMPTY"  # 本地端点（Ollama/vLLM）惯例：任意非空占位
        eval_cfg: dict[str, Any] = {
            "testset_file": str(req.work_dir / "ragas_testset.json"),
            "critic_llm": {
                "model_name": judge["model"],
                "provider": judge.get("provider", "openai"),
                "api_base": judge.get("base_url"),
                "api_key": api_key,
                "temperature": float(judge.get("temperature", 0.0)),
                **({"max_tokens": judge["max_tokens"]}
                   if judge.get("max_tokens") else {}),
            },
            "metrics": metrics,
            "language": cfg.get("language", "chinese"),
            "raise_exceptions": bool(cfg.get("raise_exceptions", False)),
            **({"batch_size": int(cfg["batch_size"])}
               if cfg.get("batch_size") else {}),
        }
        eval_cfg["embeddings"] = {
            "model_name_or_path": embeddings["model_name_or_path"],
            "provider": embeddings.get("provider", "huggingface"),
            **({"api_base": embeddings["api_base"]} if embeddings.get("api_base") else {}),
            "api_key": _resolve_api_key(embeddings),
        }
        return {"eval_backend": "RAGEval",
                "eval_config": {"tool": "ragas", "eval": eval_cfg}}

    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        cases = await self._load_cases(req)
        to_invoke, ready = self._split_modes(cases)
        if to_invoke and not (req.target or {}).get("model"):
            raise ValueError(
                "rageval：数据集无现成 response 且未配 target——配 target 走方案 A，"
                "或给数据集补 response 字段走方案 B")
        records = []
        if to_invoke:
            answers = await self._invoke_answers(req, to_invoke)
            ready = list(ready) + list(to_invoke)
        else:
            answers = {}
        by_id = {c.id: c for c in cases}
        for case_id, case in by_id.items():
            resp_text = answers.get(case_id) or str(case.metadata.get("response") or "")
            records.append(self._testset_record(case, resp_text))

        req.work_dir.mkdir(parents=True, exist_ok=True)
        testset_path = req.work_dir / "ragas_testset.json"
        testset_path.write_text(json.dumps(records, ensure_ascii=False, indent=2),
                                encoding="utf-8")

        cfg = self.build_config(req)
        judge_cfg = req.rag_eval.get("judge") or {}
        _patch_ragas_langchain_compat()  # 先垫 import 面（ragas 包初始化会撞 community 0.4 缺口）
        with contextlib.suppress(ModuleNotFoundError):
            _patch_ragas_instructor_model_args(judge_cfg)  # ragas 未安装：紧随的 evalscope 导入报错给指引
        eval_metrics = cfg["eval_config"]["eval"]["metrics"]
        req.progress_cb and req.progress_cb(
            {"phase": "ragas-scoring", "done": 0, "total": len(records),
             "note": f"{len(eval_metrics)} 指标 × {len(records)} 条"})
        try:
            from evalscope import run_task  # 进程内依赖；装在统一 evalscope 环境
        except ImportError as exc:
            raise RuntimeError(
                "evalscope 不可导入：RAGAS 后端需 pip install 'evalscope[rag]'"
                f"（原错误：{exc}）") from exc
        await run_sync_in_thread(run_task, cfg)
        return self.collect(req)

    def collect(self, req: ToolRunRequest) -> ToolRunResult:
        """解析 ``ragas_testset_score.json``（ragas 逐 case 分数）→ 指标行/失败明细。"""
        score_path = req.work_dir / "ragas_testset_score.json"
        result = ToolRunResult()
        if not score_path.exists():
            result.failures.append({
                "dataset": "rageval", "case": "-",
                "detail": f"score file missing: {score_path}"})
            return result
        result.report_files = {
            "score": str(score_path),
            "testset": str(req.work_dir / "ragas_testset.json"),
        }
        rows = json.loads(score_path.read_text(encoding="utf-8"))
        metrics = (req.rag_eval or {}).get("metrics") or []
        label = "rageval:ragas"
        for metric in metrics:
            values, failed = [], []
            for idx, row in enumerate(rows):
                v = row.get(metric)
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    failed.append((idx, row))
                    continue
                values.append(float(v))
            if not values:
                result.rows.append(ToolMetricRow(
                    dataset=label, metric=metric, value=0.0, count=0,
                    threshold=req.metric_thresholds.get(metric),
                    passed=False if req.metric_thresholds.get(metric) else None))
            else:
                value = sum(values) / len(values)
                threshold = req.metric_thresholds.get(metric)
                result.rows.append(ToolMetricRow(
                    dataset=label, metric=metric, value=round(value, 6),
                    count=len(values), threshold=threshold,
                    passed=(value >= threshold) if threshold is not None else None))
            for idx, row in failed:
                result.failures.append({
                    "dataset": label, "case": f"{metric}:{idx}",
                    "detail": f"{metric} 未得分（判官失败/NaN）："
                              f"{str(row.get('user_input'))[:80]}"})
        return result
