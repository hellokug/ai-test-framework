"""数据集管理的路径与在线源设置（``./data/dataset_settings.json``）。

四个键（全部可空，空 = 回到内置默认）：
- ``datasets_dir``：数据集文件根（默认 ``./data``）——本地数据集固定落位
  ``<根>/datasets/<类型>/``，外部注册表 ``aite_datasets.json`` 也随根走；
- ``cache_dir``：在线数据集下载缓存根（默认 ``./data/cache/hf``），注入 ``HF_HOME``；
- ``hf_endpoint``：HF 镜像仓地址（和 HF 通时用；空 = config 默认 hf-mirror.com）；
- ``remote_url``：远程拉取地址（和 HF 不通的内网场景：OBS 桶 https 地址或
  其他可直链下载数据集的地址；结尾带 ``/`` 时按 ``<地址>/<数据集名>.jsonl`` 拉取）。

解析优先级一律「环境变量 > 设置文件 > config 默认」（原则 5 显式优于隐式）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from aite import config

FIELDS = ("datasets_dir", "cache_dir", "hf_endpoint", "remote_url")

_FIELD_HELP = {
    "datasets_dir": "数据集文件根（<根>/datasets/<type>/），AITE_DATASETS_DIR 可覆盖",
    "cache_dir": "在线下载缓存根（注入 HF_HOME），HF_HOME 可覆盖",
    "hf_endpoint": "HF 镜像仓地址（空=默认镜像），HF_ENDPOINT 可覆盖",
    "remote_url": "远程拉取地址（OBS 桶/其他直链，HF 不通时用），AITE_DATASET_REMOTE_URL 可覆盖",
}


def settings_path() -> Path:
    return Path(config.DATASET_SETTINGS_FILE)


def load_settings() -> dict[str, str]:
    """读设置文件并与默认值合并；不存在/损坏/含未知键时降级（不抛错）。"""
    values = {k: "" for k in FIELDS}
    path = settings_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        if isinstance(data, dict):
            for key in FIELDS:
                val = data.get(key)
                if isinstance(val, str):
                    values[key] = val.strip()
    return values


def save_settings(patch: dict) -> dict[str, str]:
    """合并写入设置项；未知键/非字符串/非法 URL 显式报错（原则 9）。"""
    unknown = set(patch) - set(FIELDS)
    if unknown:
        raise ValueError(
            f"未知设置项 {sorted(unknown)}（合法：{'/'.join(FIELDS)}）")
    values = load_settings()
    for key, val in patch.items():
        if not isinstance(val, str):
            raise ValueError(f"设置项 {key} 须为字符串，收到 {type(val).__name__}")
        val = val.strip()
        if (key in ("hf_endpoint", "remote_url") and val
                and not val.startswith(("http://", "https://"))):
            raise ValueError(
                f"{key} 须为 http(s) 地址（OBS 桶用 https 访问地址），收到 {val!r}")
        if key in ("datasets_dir", "cache_dir") and not val:
            raise ValueError(f"{key} 不能为空（留空路径无法落盘）")
        values[key] = val
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return values


def resolve_datasets_dir() -> Path:
    env = os.environ.get(config.ENV_DATASETS_DIR)
    if env:
        return Path(env)
    configured = load_settings()["datasets_dir"]
    return Path(configured or config.DEFAULT_DATASETS_DIR)


def resolve_cache_dir() -> Path:
    env = os.environ.get("HF_HOME")
    if env:
        return Path(env)
    configured = load_settings()["cache_dir"]
    return Path(configured or config.DEFAULT_DATASET_CACHE_DIR)


def resolve_hf_endpoint() -> str:
    env = os.environ.get("HF_ENDPOINT")
    if env:
        return env
    configured = load_settings()["hf_endpoint"]
    return configured or config.DEFAULT_HF_ENDPOINT


def resolve_remote_url() -> str:
    env = os.environ.get(config.ENV_DATASET_REMOTE_URL)
    if env:
        return env
    return load_settings()["remote_url"]


def snapshot() -> dict:
    """设置全貌：原始值 + 解析值 + 各键是否被环境变量锁定（前端展示用）。"""
    values = load_settings()
    env_locked = {
        "datasets_dir": bool(os.environ.get(config.ENV_DATASETS_DIR)),
        "cache_dir": bool(os.environ.get("HF_HOME")),
        "hf_endpoint": bool(os.environ.get("HF_ENDPOINT")),
        "remote_url": bool(os.environ.get(config.ENV_DATASET_REMOTE_URL)),
    }
    defaults = {
        "datasets_dir": config.DEFAULT_DATASETS_DIR,
        "cache_dir": config.DEFAULT_DATASET_CACHE_DIR,
        "hf_endpoint": config.DEFAULT_HF_ENDPOINT,
        "remote_url": "",
    }
    return {
        "settings": values,
        "resolved": {
            "datasets_dir": str(resolve_datasets_dir()),
            "cache_dir": str(resolve_cache_dir()),
            "hf_endpoint": resolve_hf_endpoint(),
            "remote_url": resolve_remote_url(),
        },
        "env_locked": env_locked,
        "defaults": defaults,
        "help": _FIELD_HELP,
    }
