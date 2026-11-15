"""把 HuggingFace 数据集下载转换为 aite 离线数据集并注册为 builtin。

Python API::

    from aite.datasets.add_hf import add_hf_dataset
    add_hf_dataset("openai/gsm8k", name="gsm8k_full", split="test", n=200)

CLI::

    aite dataset add-hf openai/gsm8k --name gsm8k_full --split test -n 200

依赖 ``pip install 'aite[bench]'``（datasets 库），执行时需要网络；
国内镜像：设置 ``HF_ENDPOINT=https://hf-mirror.com``。
产物完全离线：``./data/datasets/accuracy/<name>.jsonl`` + 外部注册表条目。
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aite.datasets import userstore
from aite.datasets.local import _CONTEXT_KEYS, _EXPECTED_KEYS, _INPUT_KEYS, conversation_texts


def convert_records(records: list[dict], fields: dict[str, str] | None = None) -> list[dict]:
    """HF 原始记录 → aite 标准 JSONL 记录（question / answer / retrieval_context）。

    - ``fields``：显式映射，如 ``{"input": "question", "expected": "answer"}``；
      缺省按常见字段名自动识别（复用 local 加载器的键表）。
    - choices + 整数答案 → 转字母（如 cais/mmlu 的 ``answer: 2`` → ``"C"``）。
    - 未映射字段原样保留（choices 等，加载时自动进 metadata）。
    - 纯对话记录（ShareGPT conversations / OpenAI messages 等，无扁平输入键）
      原样保留不拍平：语料池与压测负载加载器直接消费话轮数组；扁平整条丢弃
      会让这类数据集导入即空。``fields`` 显式映射时仍按映射拍平。
    """
    mapping = fields or {}
    out: list[dict] = []
    for i, rec in enumerate(records):
        src_in = mapping.get("input") or next((k for k in _INPUT_KEYS if k in rec), None)
        if src_in is None:
            if not mapping and conversation_texts(rec):
                out.append(dict(rec))
            continue
        src_exp = mapping.get("expected") or next(
            (k for k in _EXPECTED_KEYS if k in rec and k != src_in), None)
        src_ctx = mapping.get("retrieval_context") or next(
            (k for k in _CONTEXT_KEYS if k in rec and k not in (src_in, src_exp)), None)
        item: dict[str, Any] = {
            "id": str(rec.get("id") or rec.get("case_id") or f"case-{i}"),
            "question": rec[src_in],
        }
        if src_exp is not None:
            val = rec[src_exp]
            choices = rec.get("choices")
            if isinstance(val, int) and isinstance(choices, list) and 0 <= val < len(choices):
                val = chr(ord("A") + val)  # 多选题答案索引 → 字母
            item["answer"] = val
        if src_ctx is not None:
            ctx = rec[src_ctx]
            item["retrieval_context"] = ctx if isinstance(ctx, list) else [ctx]
        for k, v in rec.items():  # 其余字段保留（choices / scenario 等）
            if k not in (src_in, src_exp, src_ctx, "id", "case_id"):
                item.setdefault(k, v)
        out.append(item)
    return out


def sanitize_name(name: str) -> str:
    """builtin id 只允许字母数字、下划线、连字符。"""
    return re.sub(r"[^0-9A-Za-z_-]+", "-", name).strip("-_") or "dataset"


def _jsonl_line(item: dict) -> str:
    """单行 JSONL 序列化：ensure_ascii=False 省体积，但 U+2028/U+2029 是合法
    JSON 字符却会被按行分割（splitlines / JS）切断字符串——转义保住逐行记录。"""
    return (json.dumps(item, ensure_ascii=False)
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _apply_hf_env() -> None:
    """HF 端点/缓存目录注入（环境变量优先，其次数据集设置文件）。

    huggingface_hub 在 import 期把 HF_ENDPOINT 固化进 constants.ENDPOINT，
    服务进程里可能已随其他模块提前 import——环境变量照常生效，显式改
    settings 后需要补一发 constants 同步（best-effort，未装则跳过）。
    """
    import os

    from aite.datasets import settings as ds_settings

    endpoint = ds_settings.resolve_hf_endpoint()
    os.environ.setdefault("HF_ENDPOINT", endpoint)
    os.environ.setdefault("HF_HOME", str(ds_settings.resolve_cache_dir()))
    try:
        from huggingface_hub import constants as hf_constants
        hf_constants.ENDPOINT = os.environ["HF_ENDPOINT"].rstrip("/")
    except ImportError:
        pass


def add_hf_dataset(
    hf_id: str,
    name: str | None = None,
    *,
    split: str = "test",
    subset: str | None = None,
    n: int | None = None,
    seed: int = 42,
    fields: dict[str, str] | None = None,
    data_dir: Path | None = None,
) -> Path:
    """下载 HF 数据集 → 字段映射 → 预采样 → 写 ``./data/datasets/<name>.jsonl`` → 注册。

    返回写出的 JSONL 路径；之后 ``{type: builtin, id: <name>}`` 完全离线可用。
    名称冲突时直接覆盖同名外部条目（不影响包内置数据集的文件）。
    """
    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise RuntimeError("datasets lib not installed: pip install 'aite[bench]'") from exc

    _apply_hf_env()
    ds = load_dataset(hf_id, subset, split=split)
    converted = convert_records([dict(r) for r in ds], fields)
    if not converted:
        raise ValueError(
            f"no usable records from {hf_id} (no input-like field); "
            "specify fields={'input': ..., 'expected': ...}")
    if n is not None and n < len(converted):
        converted = random.Random(seed).sample(converted, n)

    ds_id = sanitize_name(name or hf_id.split("/")[-1].lower())
    root = Path(data_dir) if data_dir else userstore.data_root()
    out_dir = root / "datasets" / "accuracy"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ds_id}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for item in converted:
            fh.write(_jsonl_line(item) + "\n")

    userstore.upsert(ds_id, {
        "file": f"datasets/accuracy/{ds_id}.jsonl",
        "external": True,
        "upstream": f"hf:{hf_id}" + (f" ({subset})" if subset else ""),
        "split": split,
        "n": len(converted),
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return path
