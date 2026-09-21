"""私域数据集生成器：基于领域种子语料的 RAG 评测集（确定性可复现）。

Python API::

    from aite.datasets.gen_domain import generate_records, generate_domain_dataset
    records = generate_records("finance", n=12, seed=42)
    await generate_domain_dataset(repo, "finance", "finance_private", n=12, seed=42)

CLI::

    aite dataset gen-domain finance --name finance_private -n 12 --seed 42

设计约定：
- 语料种子在 ``aite.datasets.domain_corpora``（公开通用事实性内容，扩充语料即
  扩充可生成题量）；同 (domain, n, seed, distractors) 输出逐字节一致，
  catalog ``version=seed<N>`` 即可复现凭证。
- 产物 JSONL 落 ``./data/datasets/<name>.jsonl``（用户数据区，不入包源码，
  同 add-hf 约定），注册 userstore 外部注册表（带 recommend，``metrics: auto``
  可用）+ dataset catalog 的 accuracy 条目。
- 记录形态 = 标准 RAG 形状：question / answer / retrieval_context
  （gold 文档 + N 篇同域干扰文档，乱序模拟真实检索）。
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any

from aite.datasets import userstore
from aite.datasets.domain_corpora import DOMAIN_CORPORA

# 生成的 RAG 数据集默认推荐指标：RAGAS 五件套（无 gateway 走规则口径，
# 有 gateway 时 faithfulness / context_precision / context_recall 自动升级 LLM 判定）
DEFAULT_RECOMMEND = [
    {"name": "faithfulness", "threshold": 0.5},
    {"name": "context_recall", "threshold": 0.5},
    {"name": "context_precision", "threshold": 0.5},
    {"name": "answer_correctness", "threshold": 0.4},
]


def list_domains() -> list[str]:
    return sorted(DOMAIN_CORPORA)


def corpus_size(domain: str) -> int:
    return len(DOMAIN_CORPORA[domain]["qa"])


def generate_records(domain: str, n: int, seed: int = 42,
                     distractors: int = 2) -> list[dict[str, Any]]:
    """从领域语料抽样生成 RAG 记录（乱序题序 + 干扰文档乱序）。

    n 超过语料出题池时显式报错（不重复出题，宁缺毋错）。
    """
    corpus = DOMAIN_CORPORA.get(domain)
    if corpus is None:
        raise ValueError(
            f"未知领域 {domain!r}（可选：{', '.join(list_domains())}）")
    pool = list(corpus["qa"])
    if n > len(pool):
        raise ValueError(
            f"领域 {domain!r} 语料池仅 {len(pool)} 题，无法生成 {n} 条"
            "（减少 n 或扩充 domain_corpora 语料）")
    rng = random.Random(f"{domain}:{seed}")
    docs = {d["id"]: d for d in corpus["docs"]}
    records: list[dict[str, Any]] = []
    for i, qa in enumerate(rng.sample(pool, n)):
        gold = docs[qa["doc"]]
        others = [d for d in corpus["docs"] if d["id"] != qa["doc"]]
        noise = rng.sample(others, min(distractors, len(others)))
        contexts = [f"【{d['title']}】{d['text']}" for d in noise]
        contexts.append(f"【{gold['title']}】{gold['text']}")
        rng.shuffle(contexts)
        records.append({
            "id": f"{domain}-{i:03d}",
            "question": qa["question"],
            "answer": qa["answer"],
            "retrieval_context": contexts,
            "domain": domain,
            "topic": qa.get("topic", ""),
            "gold_doc": gold["title"],
        })
    return records


async def generate_domain_dataset(
    repo: Any, domain: str, name: str, *, n: int, seed: int = 42,
    distractors: int = 2, notes: str = "",
) -> dict:
    """生成 → 落盘 ``./data/datasets/<name>.jsonl`` → 注册 userstore + catalog。

    名称与包内置（非外部）数据集冲突时显式报错（外部同名条目按 add-hf
    惯例覆盖）。返回 catalog 条目。
    """
    from aite.datasets import builtin as builtin_ds
    from aite.datasets.catalog import register_entry

    merged = builtin_ds.merged_info()
    if name in merged and not merged[name].get("external"):
        raise ValueError(f"数据集名 {name!r} 与包内置数据集冲突，请换一个名字")
    records = generate_records(domain, n, seed, distractors)

    out_dir = userstore.data_root() / "datasets"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    userstore.upsert(name, {
        "file": f"datasets/{name}.jsonl",
        "external": True,
        "upstream": f"aite-gen:{domain}",
        "task": f"私域{DOMAIN_CORPORA[domain]['label']}领域 RAG 评测集（aite-gen 生成）",
        "n": len(records),
        "seed": seed,
        "recommend": DEFAULT_RECOMMEND,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return await register_entry(
        repo, dataset_type="accuracy", name=name, source="generated",
        path=str(path).replace("\\", "/"), version=f"seed{seed}",
        stats={"count": len(records), "domain": domain, "distractors": distractors},
        notes=notes or f"generated from {domain} seed corpus")
