"""Builtin metrics. Importing registers everything (rule/stats/perf/reliability/
agent/safety + LLM helpers).

定位红线（2026-09-23，CLAUDE.md「架构与目标」）：不造任何评估指标，新增评测能力
一律走 aite/tools/ 开源引擎适配器（RAG 评测 → evalscope RAGEval 的 RAGAS 后端）。
本目录冻结为存量兼容：自研 RAGAS 五件套（rag_metrics/ragas_metrics/embedding）
已于迁移启动时删除，judge/safety/统计类按 docs/roadmap.md「存量自研指标迁移」
逐项对齐后替换。
"""

from aite.metrics import (  # noqa: F401
    agent_metrics,
    judge,  # noqa: F401
    llm_based,
    nlp_stats,
    performance,
    reliability,
    rules,
    safety,
    swebench,
)
