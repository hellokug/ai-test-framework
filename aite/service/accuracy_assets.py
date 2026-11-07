"""精度域脚本资产层（V1-PLAN §10.4 accuracy 形态）。

脚本 = 固化的域任务 spec + 名字（spec = suite raw 同构，与
``examples/accuracy/*.yaml``、``POST /accuracy/tasks`` 的 spec 完全同形；
YAML 只是序列化形态）。目录契约::

    examples/accuracy/<id>.yaml          # builtin 预置：随框架发布，只读可复制
    <config.DEFAULT_ACCURACY_SCRIPTS_DIR>/<id>.yaml
                                         # 自定义：文档 = {id, name, description,
                                         #   spec, created_at, updated_at}

预置文件是裸 suite raw（无包装文档），列表/详情按 doc 形态即时包装；自定义
保存经 resolve_suite 真实校验（工具域轻校验 / 自研域 SuiteConfig 强校验），
运行参数（目标模型覆盖/版本/执行方式）不沉淀，由发起任务时覆盖。文件写出
强制 LF（Windows 宿主 CRLF 教训，见 roadmap 2026-10-11）。
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from aite import config
from aite.utils.logging import get_logger

logger = get_logger("aite.service.accuracy_assets")

SCRIPT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


def _builtin_root() -> Path:
    """仓库 examples/accuracy（随包发布，与运行目录无关）。"""
    return Path(__file__).resolve().parents[2] / "examples" / "accuracy"


def _custom_root() -> Path:
    return Path(config.DEFAULT_ACCURACY_SCRIPTS_DIR)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()   # 3.10 兼容（datetime.UTC 是 3.11+）


def _write_text(path: Path, text: str) -> None:
    """原子写出（tmp + os.replace）：崩溃不留半截脚本文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _desc_line(text: str) -> str:
    """多行 description 取首个非空行（脚本名展示口径）。"""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _wrap_builtin(path: Path) -> dict[str, Any] | None:
    """预置裸 suite raw → 脚本 doc；损坏文件跳过（服务端告警，前端无感）。"""
    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise ValueError("not a mapping")
    except Exception as exc:   # noqa: BLE001 - 单文件损坏不推翻整个脚本库
        logger.warning("精度预置脚本 %s 解析失败，已跳过: %s", path.name, exc)
        return None
    sid = path.stem
    desc = str(spec.get("description") or "")
    return {
        "id": sid,
        "name": _desc_line(desc) or str(spec.get("run_name") or sid),
        "description": desc,
        "spec": spec,
        "source": "builtin",
        "created_at": None,
        "updated_at": None,
    }


def _custom_path(sid: str) -> Path:
    return _custom_root() / f"{sid}.yaml"


def _load_custom(sid: str) -> dict[str, Any]:
    path = _custom_path(sid)
    if not path.is_file():
        raise FileNotFoundError(sid)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "spec" not in doc:
        raise ValueError(f"脚本 {sid} 文档损坏（缺 spec 字段）")
    doc.setdefault("id", sid)
    doc["source"] = "custom"
    return doc


def _validate_spec(spec: Any) -> dict:
    """保存前真实校验：走 resolve_suite（工具域轻校验 / 自研域强校验）。"""
    if not isinstance(spec, dict):
        raise ValueError("spec 须为对象（suite raw 同构）")
    if not (spec.get("datasets") or (spec.get("tools") or {}).get("datasets")):
        raise ValueError("评测数据集不能为空（datasets / tools.datasets）")
    from aite.service.run_service import resolve_suite

    try:
        resolve_suite(spec)
    except ValueError as exc:
        raise ValueError(f"spec 校验失败：{exc}") from exc
    return spec


def list_scripts(source: str | None = None) -> dict[str, Any]:
    """builtin + 自定义合并（同 id 自定义优先展示——预置不可被自定义覆盖，
    保存时即 409，此处仅防御性排序）。"""
    items: list[dict[str, Any]] = []
    for path in sorted(_builtin_root().glob("*.yaml")):
        doc = _wrap_builtin(path)
        if doc is not None:
            items.append(doc)
    if _custom_root().is_dir():
        for path in sorted(_custom_root().glob("*.yaml")):
            try:
                items.append(_load_custom(path.stem))
            except Exception as exc:   # noqa: BLE001 - 损坏文件跳过不推翻列表
                logger.warning("精度自定义脚本 %s 读取失败，已跳过: %s", path.name, exc)
    if source:
        items = [d for d in items if d["source"] == source]
    return {"items": items, "count": len(items)}


def get_script(sid: str) -> dict[str, Any]:
    if _custom_path(sid).is_file():
        return _load_custom(sid)
    builtin = _builtin_root() / f"{sid}.yaml"
    if builtin.is_file():
        doc = _wrap_builtin(builtin)
        if doc is not None:
            return doc
    raise FileNotFoundError(sid)


def save_script(sid: str, name: str, description: str, spec: dict) -> dict:
    """新建自定义脚本：id 规则 + 预置/同名冲突 409 + spec 真实校验。"""
    if not SCRIPT_ID_RE.match(sid or ""):
        raise ValueError(f"脚本 id 不合法（须匹配 {SCRIPT_ID_RE.pattern}）：{sid!r}")
    if (_builtin_root() / f"{sid}.yaml").is_file():
        raise FileExistsError(f"{sid} 与研发预置脚本重名（预置只读，请换 id）")
    path = _custom_path(sid)
    if path.exists():
        raise FileExistsError(sid)
    _validate_spec(spec)
    doc = {"id": sid, "name": name or sid, "description": description,
           "spec": spec, "created_at": _now(), "updated_at": _now()}
    _write_text(path, yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    return _load_custom(sid)


def update_script(sid: str, name: str | None = None, description: str | None = None,
                  spec: dict | None = None) -> dict:
    """编辑自定义脚本（编辑即生效；历史任务持创建时快照不受影响）。"""
    if not _custom_path(sid).is_file():
        if (_builtin_root() / f"{sid}.yaml").is_file():
            raise PermissionError(f"{sid} 为研发预置脚本（只读），请复制出自定义副本后修改")
        raise FileNotFoundError(sid)
    doc = _load_custom(sid)
    if spec is not None:
        _validate_spec(spec)
        doc["spec"] = spec
    if name is not None:
        doc["name"] = name
    if description is not None:
        doc["description"] = description
    doc["updated_at"] = _now()
    _write_text(_custom_path(sid), yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    return _load_custom(sid)


def delete_script(sid: str) -> None:
    """删除自定义脚本；预置只读 409。"""
    if not _custom_path(sid).is_file():
        if (_builtin_root() / f"{sid}.yaml").is_file():
            raise PermissionError(f"{sid} 为研发预置脚本（只读），不可删除")
        raise FileNotFoundError(sid)
    _custom_path(sid).unlink()
