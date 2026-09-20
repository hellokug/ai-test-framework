"""数据集管理统一目录服务（docs/domain-backends.md §12）。

一条登记 = 一个受管数据集；类型区分（functional/accuracy/perf）、外壳共用。
本模块只做类型无关的登记与引用解析；分类型校验器在各域
（functional → aite.functional.ingest，accuracy → add_hf/local，
perf → tools.perf_dataset 生成入口）。
"""

from __future__ import annotations

from typing import Any

_DATASET_TYPES = ("functional", "accuracy", "perf")


def check_type(dataset_type: str) -> str:
    if dataset_type not in _DATASET_TYPES:
        raise ValueError(
            f"未知数据集类型 {dataset_type!r}（合法：{'/'.join(_DATASET_TYPES)}）")
    return dataset_type


async def register_entry(repo: Any, *, dataset_type: str, name: str,
                         source: str, path: str, version: str = "",
                         stats: dict | None = None, notes: str = "",
                         schema_version: str = "1") -> dict:
    """按 (type, name) 幂等登记/更新一条目录（repo.upsert 保证竞态安全）。"""
    check_type(dataset_type)
    return await repo.upsert_dataset_catalog({
        "type": dataset_type, "name": name, "version": version,
        "source": source, "schema_version": schema_version,
        "stats": stats or {}, "path": path, "notes": notes,
    })


async def resolve_dataset_ref(repo: Any, ref: str) -> dict:
    """解析任务 spec 的数据集引用：id → type:name → 全局唯一 name。

    解析失败/跨类型重名显式报错（原则 9），返回 catalog 条目 dict。
    """
    row = await repo.get_dataset_catalog(ref)
    if row is None and ":" in ref:
        type_part, _, name_part = ref.partition(":")
        row = await repo.find_dataset_catalog(type_part, name_part)
    if row is None:
        matches = [r for r in await repo.list_dataset_catalog(limit=1000)
                   if r["name"] == ref]
        if len(matches) == 1:
            row = matches[0]
        if len(matches) > 1:
            types = "/".join(sorted(m["type"] for m in matches))
            raise ValueError(
                f"数据集引用 {ref!r} 跨类型重名（{types}），请用 type:name 形式")
    if row is None:
        raise ValueError(f"未知数据集引用: {ref!r}（aite dataset list 查看）")
    return row
