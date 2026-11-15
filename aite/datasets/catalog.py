"""数据集管理统一目录服务（docs/domain-backends.md §12）。

一条登记 = 一个受管数据集；类型区分（functional/accuracy/perf）、外壳共用。
本模块只做类型无关的登记与引用解析；分类型校验器在各域
（functional → aite.functional.ingest，accuracy → add_hf/local，
perf → tools.perf_dataset 生成入口）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_DATASET_TYPES = ("functional", "accuracy", "perf")

# 评测能力维度（平台统一画像：导入抽屉 chips、目录「能力」筛选、catalog 存储
# 三处同源，key 落库、中文标签只在前端展示）。evalscope 基准行由
# tags_to_capabilities 从原生 tags 映射（MCQ/Chinese 等格式/语言类 tag 不映射）。
CAPABILITIES: dict[str, str] = {
    "knowledge": "基础知识", "math": "数学", "code": "代码",
    "reasoning": "推理", "instruction": "指令遵循", "agent": "智能体",
    "longcontext": "长文本", "safety": "安全",
}

_TAG_CAPS: dict[str, str] = {
    "knowledge": "knowledge", "commonsense": "knowledge", "qa": "knowledge",
    "readingcomprehension": "knowledge", "truthfulness": "knowledge",
    "math": "math", "coding": "code", "code": "code",
    "reasoning": "reasoning", "instructionfollowing": "instruction",
    "instruction": "instruction", "agent": "agent",
    "longcontext": "longcontext", "safety": "safety",
}


def normalize_capabilities(value: Iterable[str] | None) -> list[str]:
    """能力维度入册归一：去空白/小写/去重，未知 key 显式报错（原则 9）。"""
    if not value:
        return []
    caps: list[str] = []
    for raw in value:
        key = str(raw).strip().lower()
        if key not in CAPABILITIES:
            raise ValueError(
                f"未知能力维度 {raw!r}（合法：{'/'.join(CAPABILITIES)}）")
        if key not in caps:
            caps.append(key)
    return caps


def tags_to_capabilities(tags: Iterable[str] | None) -> list[str]:
    """evalscope 原生 tags → 平台能力维度（顺序按首个命中 tag，去重保序）。"""
    caps: list[str] = []
    for tag in tags or []:
        key = _TAG_CAPS.get(str(tag).strip().lower())
        if key and key not in caps:
            caps.append(key)
    return caps


# 来源私有性（open=开源公开数据集 / self=自建自有数据）。与 origin（获取渠道
# local/online）正交：内网 OBS 直链拉取的自有语料 origin=online 但 ownership=self；
# 存储位置（本地/在线）是第三个维度，本机登记行恒为本地，只有基准菜单行有
# 「未下载=在线」状态，由前端按下载标记判定。
OWNERSHIP_VALUES = ("open", "self")


def normalize_ownership(value: str | None) -> str:
    key = str(value or "").strip().lower()
    if key not in OWNERSHIP_VALUES:
        raise ValueError(
            f"未知来源（私有性）{value!r}（合法：{'/'.join(OWNERSHIP_VALUES)}）")
    return key


def ownership_from_source(source: str, upstream: str = "") -> str:
    """来源缺省推导：HF 镜像导入（或注册表 upstream hf:）=开源，其余=自建。"""
    if source == "hf" or upstream.startswith("hf:"):
        return "open"
    return "self"


def check_type(dataset_type: str) -> str:
    if dataset_type not in _DATASET_TYPES:
        raise ValueError(
            f"未知数据集类型 {dataset_type!r}（合法：{'/'.join(_DATASET_TYPES)}）")
    return dataset_type


async def register_entry(repo: Any, *, dataset_type: str, name: str,
                         source: str, path: str, version: str = "",
                         stats: dict | None = None, notes: str = "",
                         schema_version: str = "1",
                         capabilities: Iterable[str] | None = None,
                         ownership: str | None = None) -> dict:
    """按 (type, name) 幂等登记/更新一条目录（repo.upsert 保证竞态安全）。

    capabilities=None 表示本次登记不动能力维度（场景生成等既有调用方不传即
    保留旧值）；传列表（含空列表）则归一后整体覆盖。ownership=None 按
    source 推导（hf=开源，其余=自建），显式传入则归一后采用。
    """
    check_type(dataset_type)
    entry: dict[str, Any] = {
        "type": dataset_type, "name": name, "version": version,
        "source": source, "schema_version": schema_version,
        "stats": stats or {}, "path": path, "notes": notes,
        "ownership": (normalize_ownership(ownership) if ownership is not None
                      else ownership_from_source(source)),
    }
    if capabilities is not None:
        entry["capabilities"] = normalize_capabilities(capabilities)
    return await repo.upsert_dataset_catalog(entry)


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
