"""功能型数据集摄取：cases.jsonl → 校验 → 落盘 → 登记 catalog（§12.2）。

一个功能数据集 = 一个场景的 cases.jsonl；文件区布局
``./data/datasets/functional/<name>/cases.jsonl`` 天然构成 pytest 适配器的
场景根（``tests_dir: ./data/datasets/functional`` + ``datasets: [<name>]``
零改动即消费）。校验复用 harness.Case 真实校验器——断言词表封闭，坏行
显式报错不静默（原则 9）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from aite.datasets.catalog import register_entry
from aite.functional.harness import Case

FUNCTIONAL_ROOT = Path("./data/datasets/functional")


def validate_cases_file(path: str | Path) -> dict:
    """逐行过 harness.Case 校验，返回 stats（case 数 + 断言键分布）。"""
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"功能数据集文件不存在: {src}")
    assertion_keys: dict[str, int] = {}
    count = 0
    for lineno, line in enumerate(
            src.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{src} 第 {lineno} 行不是合法 JSON: {exc}") from exc
        case = Case(raw, f"{src}#L{lineno}")     # 校验失败抛 ValueError
        for key in case.expect:
            assertion_keys[key] = assertion_keys.get(key, 0) + 1
        count += 1
    if count == 0:
        raise ValueError(f"{src}: 没有有效用例（至少一行 cases.jsonl）")
    return {"count": count, "assertion_keys": assertion_keys}


async def ingest_functional(repo: Any, src_path: str | Path, name: str,
                            notes: str = "", force: bool = False) -> dict:
    """校验 → 拷入文件区 → 登记 catalog；同名已存在时须 force 覆盖。"""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError(f"非法数据集名: {name!r}")
    src = Path(src_path)
    stats = validate_cases_file(src)
    existing = await repo.find_dataset_catalog("functional", name)
    if existing and not force:
        raise ValueError(f"功能数据集 {name!r} 已存在（--force 覆盖）")
    dest_dir = FUNCTIONAL_ROOT / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "cases.jsonl"
    shutil.copyfile(src, dest)
    return await register_entry(
        repo, dataset_type="functional", name=name, source="local",
        path=str(dest).replace("\\", "/"), stats=stats, notes=notes)
