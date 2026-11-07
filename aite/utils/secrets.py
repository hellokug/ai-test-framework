"""本地密钥库：api_key_env 指向的环境变量未设置时的加密兜底存储（V1-PLAN §10.5）。

- 存储：``SECRET_STORE_FILE``（JSON：name → {enc, mask}），密钥文件
  ``SECRET_KEY_FILE``（随机 32B，与库分离）；两者均在 data/ 下不入 git。
- 加密：本地密钥派生 SHA-256 流加密（nonce 随机）——威胁模型为单机单用户工具，
  防「数据库/文件被随手翻看」，不对抗本机攻击者；界面与 REST 只暴露掩码。
- 解析优先级：**环境变量 > 密钥库**（显式优于隐式）；解析失败降级 default 并告警。

运行时收口：所有 ``api_key_env`` 的 ``os.environ.get`` 解析统一走
``resolve_secret``（tools/base、core/loader、reliability_tasks、rageval_adapter）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from aite import config

logger = logging.getLogger("aite.utils.secrets")


def _key_file() -> Path:
    return Path(config.SECRET_KEY_FILE)


def _store_file() -> Path:
    return Path(config.SECRET_STORE_FILE)


def _load_key() -> bytes:
    key_file = _key_file()
    if key_file.exists():
        return bytes.fromhex(key_file.read_text(encoding="utf-8").strip())
    key_file.parent.mkdir(parents=True, exist_ok=True)
    raw = os.urandom(32)
    key_file.write_text(raw.hex() + "\n", encoding="utf-8")
    return raw


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(a ^ b for a, b in zip(data, out, strict=False))


def encrypt(plain: str) -> str:
    key = _load_key()
    nonce = os.urandom(16)
    enc = _xor_stream(plain.encode("utf-8"), key, nonce)
    return base64.b64encode(nonce + enc).decode("ascii")


def decrypt(token: str) -> str:
    key = _load_key()
    raw = base64.b64decode(token)
    nonce, enc = raw[:16], raw[16:]
    return _xor_stream(enc, key, nonce).decode("utf-8")


def mask(value: str) -> str:
    if not value:
        return "—"
    return value[:3] + "****" + value[-2:] if len(value) > 8 else "****"


def _load_store() -> dict[str, Any]:
    store_file = _store_file()
    if not store_file.exists():
        return {}
    try:
        return json.loads(store_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        logger.warning("密钥库文件损坏，忽略现有条目（%s）：%s", store_file, exc)
        return {}


def _save_store(store: dict[str, Any]) -> None:
    store_file = _store_file()
    store_file.parent.mkdir(parents=True, exist_ok=True)
    store_file.write_text(json.dumps(store, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def set_secret(name: str, value: str) -> str:
    """录入/覆盖密钥（密文落盘），返回掩码。"""
    if not name or not value:
        raise ValueError("密钥名与值均不能为空")
    mask_text = mask(value)
    store = _load_store()
    store[name] = {"enc": encrypt(value), "mask": mask_text}
    _save_store(store)
    return mask_text


def list_secrets() -> list[dict[str, str]]:
    """密钥清单（仅名称 + 掩码，永不返回明文/密文）。"""
    store = _load_store()
    return [{"name": name, "mask": entry.get("mask", "****")}
            for name, entry in sorted(store.items())]


def delete_secret(name: str) -> bool:
    store = _load_store()
    if name not in store:
        return False
    del store[name]
    _save_store(store)
    return True


def resolve_secret(name: str, default: str | None = None) -> str | None:
    """api_key_env 统一解析：环境变量优先，未设置查密钥库，都没有给 default。"""
    value = os.environ.get(name)
    if value:
        return value
    entry = _load_store().get(name)
    if not entry:
        return default
    try:
        return decrypt(entry["enc"])
    except Exception as exc:   # 密钥文件轮换/条目损坏：降级 default 并告警
        logger.warning("密钥库条目 %s 解密失败，按未设置处理: %s", name, exc)
        return default
