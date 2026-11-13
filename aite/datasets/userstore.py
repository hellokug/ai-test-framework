"""用户自建离线数据集的存储与外部注册表。

设计约束（项目约定）：
- 外部数据集**不进包源码**：JSONL 按类型落在项目 ``./data/datasets/<类型>/``，
  注册信息在 ``./data/aite_datasets.json``。
- 路径相对当前工作目录（与 ``./aite.db``、``./reports`` 的约定一致），
  在项目根目录执行命令即可。
- builtin 加载器启动时将外部注册表与内置 ``BUILTIN_INFO`` 合并，
  外部条目带 ``external: true`` 标记，数据文件路径相对 ``./data``。
"""

from __future__ import annotations

import json
from pathlib import Path

REGISTRY_FILENAME = "aite_datasets.json"


def data_root() -> Path:
    """外部数据集根目录：AITE_DATASETS_DIR 环境变量 > 设置文件 > 项目 ./data。"""
    from aite.datasets import settings
    return settings.resolve_datasets_dir()


def dataset_dir(dataset_type: str) -> Path:
    """类型化数据集目录：``./data/datasets/<type>/``（functional 域既有约定的对齐推广）。"""
    from aite.datasets.catalog import check_type
    return data_root() / "datasets" / check_type(dataset_type)


def registry_path() -> Path:
    return data_root() / REGISTRY_FILENAME


def load_registry() -> dict[str, dict]:
    """读取外部注册表；不存在或损坏时返回空 dict（不抛错，降级为仅内置）。"""
    path = registry_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_registry(registry: dict[str, dict]) -> Path:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def upsert(name: str, entry: dict) -> Path:
    """新增/覆盖一个外部数据集注册项并落盘。"""
    registry = load_registry()
    registry[name] = entry
    return save_registry(registry)
