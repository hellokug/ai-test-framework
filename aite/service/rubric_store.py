"""判官评分标准（rubric）资产仓：data/rubrics.json 单文件持久化 + 内容指纹。

解决「每次用判官都重抄 rubric」的沉淀问题：平台录入一次（命名 + 文本），
评测/校准任务按 id 引用、整文落 spec（运行时快照自包含，历史可复现）。
文件仓对齐 data/dataset_settings.json 既有模式（单 JSON、原子写、零表结构）
——rubric 低频改、小体积、无检索需求，不值得为此动 storage 层（奥卡姆剃刀）。

fingerprint = sha256(文本.strip())[:12]：任务与校准报告落指纹，rubric 改一字
指纹即变，旧结论据此判定「属于哪版标准、是否已失效」。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aite.datasets.userstore import data_root


def default_path() -> Path:
    return data_root() / "rubrics.json"


def fingerprint(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"rubric 仓读取失败 {path}: {exc}") from exc
    if not isinstance(data.get("items"), list):
        raise ValueError(f"rubric 仓结构损坏（缺 items 数组）: {path}")
    return data


def _save(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _find(data: dict[str, Any], rid: str) -> dict[str, Any]:
    for item in data["items"]:
        if item.get("id") == rid:
            return item
    raise LookupError(f"rubric 不存在: {rid}")


def list_rubrics(path: Path | None = None) -> list[dict[str, Any]]:
    return _load(path or default_path())["items"]


def get_rubric(rid: str, path: Path | None = None) -> dict[str, Any]:
    return dict(_find(_load(path or default_path()), rid))


def create_rubric(name: str, content: str, score_type: str = "",
                  path: Path | None = None) -> dict[str, Any]:
    if not name.strip() or not content.strip():
        raise ValueError("rubric 名称与内容均不能为空")
    p = path or default_path()
    data = _load(p)
    if any(item.get("name") == name.strip() for item in data["items"]):
        raise FileExistsError(f"rubric 名称已存在: {name.strip()}（换名保存，或 PATCH 改既有条目）")
    item = {"id": uuid.uuid4().hex[:12], "name": name.strip(), "content": content,
            "score_type": score_type.strip(), "fingerprint": fingerprint(content),
            "created_at": _now(), "updated_at": _now()}
    data["items"].append(item)
    _save(data, p)
    return dict(item)


def update_rubric(rid: str, *, name: str | None = None, content: str | None = None,
                  score_type: str | None = None,
                  path: Path | None = None) -> dict[str, Any]:
    p = path or default_path()
    data = _load(p)
    item = _find(data, rid)
    if name is not None:
        if not name.strip():
            raise ValueError("rubric 名称不能为空")
        if any(x.get("name") == name.strip() and x["id"] != rid for x in data["items"]):
            raise FileExistsError(f"rubric 名称已存在: {name.strip()}")
        item["name"] = name.strip()
    if content is not None:
        if not content.strip():
            raise ValueError("rubric 内容不能为空")
        item["content"] = content
        item["fingerprint"] = fingerprint(content)
    if score_type is not None:
        item["score_type"] = score_type.strip()
    item["updated_at"] = _now()
    _save(data, p)
    return dict(item)


def delete_rubric(rid: str, path: Path | None = None) -> None:
    p = path or default_path()
    data = _load(p)
    item = _find(data, rid)
    data["items"].remove(item)
    _save(data, p)
