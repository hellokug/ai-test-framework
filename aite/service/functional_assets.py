"""功能域评估场景与脚本（用例）资产层（V1-PLAN §10.4/§10.5）。

目录契约（用户根 ``config.DEFAULT_FUNCTIONAL_SCENARIOS_DIR``；builtin 场景在
包内 ``aite.functional.SCENARIOS_DIR`` 只读）::

    <root>/<scenario>/manifest.json        # 显示名 / 维度标签 / 说明
    <root>/<scenario>/scripts.json         # 受管脚本登记（name/case_type/external_ref/source）
    <root>/<scenario>/scripts/<sid>.jsonl  # 脚本数据行（harness.Case 同构字典）
    <root>/<scenario>/cases.jsonl          # 派生产物 = 全部脚本行拼接（pytest 引擎零改动消费）

首次向场景写入脚本时若已存在无主 ``cases.jsonl`` → 自动收编为脚本
``legacy-cases``（case_type=迁移），此后 ``cases.jsonl`` 全量由脚本派生；
builtin 场景只读，写操作显式报错（原则 9）。脚本 id 全平台唯一（跨场景寻址
无歧义）。文件写出强制 LF（Windows 宿主 CRLF 教训，见 roadmap 2026-10-11）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError

from aite import config
from aite.datasets.catalog import resolve_dataset_ref
from aite.functional import SCENARIOS_DIR
from aite.functional.harness import Case
from aite.functional.ingest import iter_cases_lines
from aite.utils.logging import get_logger

logger = get_logger("aite.service.functional_assets")

SCRIPT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SCENARIO_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
LEGACY_SCRIPT_ID = "legacy-cases"
BUILTIN_DIM_TAGS = ("API 契约", "模型能力", "Agent")
CASE_TYPES = ("基础", "边界", "异常", "兼容", "迁移")


class ScenarioReadOnlyError(PermissionError):
    """builtin 场景只读（路由映射 409）。"""


def _user_root() -> Path:
    return Path(config.DEFAULT_FUNCTIONAL_SCENARIOS_DIR)


def _builtin_root() -> Path:
    return SCENARIOS_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()   # 3.10 兼容（datetime.UTC 是 3.11+）


def _write_text(path: Path, text: str) -> None:
    """原子写出（tmp + os.replace）：崩溃不留半截 registry/派生文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path} 不是合法 JSON: {exc}") from exc


def _is_scenario_dir(path: Path) -> bool:
    if not path.is_dir() or path.name.startswith(("_", ".")):
        return False
    names = {p.name for p in path.iterdir()}
    return bool(names & {"manifest.json", "cases.jsonl", "scripts.json"}) \
        or any(n.startswith("test_") and n.endswith(".py") for n in names)


def _load_manifest(scen: Path) -> dict:
    manifest = _read_json(scen / "manifest.json", {})
    return {"display_name": manifest.get("display_name") or scen.name,
            "dimension": manifest.get("dimension", ""),
            "description": manifest.get("description", "")}


def _registry_path(scen: Path) -> Path:
    return scen / "scripts.json"


def _load_registry(scen: Path) -> list[dict]:
    return _read_json(_registry_path(scen), [])


def _save_registry(scen: Path, entries: list[dict]) -> None:
    _write_text(_registry_path(scen), json.dumps(entries, ensure_ascii=False, indent=2))


def _script_file(scen: Path, sid: str) -> Path:
    return scen / "scripts" / f"{sid}.jsonl"


def _read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _count_cases(scen: Path) -> int:
    return len(_read_rows(scen / "cases.jsonl"))


def _scenario_summary(path: Path, source: str) -> dict:
    manifest = _load_manifest(path)
    registry = _load_registry(path)
    coverage: dict[str, int] = {}
    for entry in registry:
        n = len(_read_rows(_script_file(path, entry["id"])))
        coverage[entry.get("case_type", "未分类")] = \
            coverage.get(entry.get("case_type", "未分类"), 0) + n
    return {"id": path.name, **manifest, "source": source,
            "forms": "script" if any(
                p.name.startswith("test_") and p.name.endswith(".py")
                for p in path.iterdir()) else "jsonl",
            "scripts": len(registry),
            "cases": _count_cases(path), "coverage": coverage}


def scan_scenarios(source: str | None = None) -> list[dict]:
    """合并 builtin（只读）与用户根的场景清单；source 可选过滤。"""
    roots = [("builtin", _builtin_root()), ("user", _user_root())]
    items: list[dict] = []
    for src, root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not _is_scenario_dir(path):
                continue
            try:
                items.append(_scenario_summary(path, src))
            except ValueError as exc:   # 损坏目录跳过 + 告警，不拖垮整个列表
                logger.warning("跳过损坏的场景目录 %s: %s", path, exc)
    if source:
        items = [i for i in items if i["source"] == source]
    return sorted(items, key=lambda i: i["id"])


async def create_scenario(repo: Any, sid: str, display_name: str = "",
                          dimension: str = "", description: str = "") -> dict:
    """新建用户场景：维度标签须在标签库内（builtin + 自定义），建目录写 manifest。"""
    if not SCENARIO_ID_RE.match(sid or ""):
        raise ValueError(f"非法场景 id: {sid!r}（小写字母开头，允许数字/_，2-64 位）")
    if dimension:
        allowed = {t["name"] for t in await list_dim_tags(repo)}
        if dimension not in allowed:
            raise ValueError(f"未知维度标签: {dimension!r}"
                             f"（合法：{'/'.join(sorted(allowed))}；可先到标签库新增）")
    return await asyncio.to_thread(_create_scenario_files, sid, display_name,
                                   dimension, description)


def _create_scenario_files(sid: str, display_name: str, dimension: str,
                           description: str) -> dict:
    target = _user_root() / sid
    if target.exists() or (_builtin_root() / sid).is_dir():
        raise FileExistsError(f"场景 {sid!r} 已存在")
    manifest = {"display_name": display_name or sid, "dimension": dimension,
                "description": description}
    _write_text(target / "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2))
    return _scenario_summary(target, "user")


def _scenario_dir(scenario_id: str) -> Path:
    """用户场景目录解析；builtin 命中报只读，不存在报 404 语义。"""
    if (_builtin_root() / scenario_id).is_dir():
        raise ScenarioReadOnlyError(
            f"场景 {scenario_id!r} 为内置只读场景（随框架发布），脚本请建到自建场景")
    scen = _user_root() / scenario_id
    if not _is_scenario_dir(scen):
        raise FileNotFoundError(f"场景不存在: {scenario_id!r}")
    return scen


def _all_row_ids(scen: Path, exclude_sid: str | None = None) -> set[str]:
    ids: set[str] = set()
    for entry in _load_registry(scen):
        if exclude_sid and entry["id"] == exclude_sid:
            continue
        for row in _read_rows(_script_file(scen, entry["id"])):
            ids.add(str(row.get("id")))
    return ids


def _normalize_rows(rows: list[dict], sid: str,
                    taken: set[str]) -> list[dict]:
    """行级 harness.Case 校验 + 场景内唯一 id 分配（显式 id 优先，缺省 sid.n）。"""
    normalized: list[dict] = []
    for i, raw in enumerate(rows, start=1):
        rid = str(raw.get("id") or f"{sid}.{i}")
        if rid in taken:
            raise ValueError(f"用例 id 在场景内冲突: {rid!r}")
        data = {**raw, "id": rid}                  # 先补 id（Case 必填）再校验
        Case(data, f"script:{sid}#row{i}")         # 校验失败抛 ValueError
        taken.add(rid)
        normalized.append(data)
    if not normalized:
        raise ValueError("脚本至少需要 1 行数据")
    return normalized


def _materialize(scen: Path) -> None:
    """cases.jsonl = 全部受管脚本行拼接（引擎 cases.jsonl 契约零改动）。"""
    lines: list[str] = []
    for entry in _load_registry(scen):
        for row in _read_rows(_script_file(scen, entry["id"])):
            lines.append(json.dumps(row, ensure_ascii=False))
    _write_text(scen / "cases.jsonl", "\n".join(lines) + ("\n" if lines else ""))


def _ensure_managed(scen: Path) -> list[dict]:
    """首次写场景时收编无主 cases.jsonl 为 legacy-cases（迁移），防派生覆盖。"""
    registry = _load_registry(scen)
    if registry:
        return registry
    legacy_rows = _read_rows(scen / "cases.jsonl")
    if not legacy_rows:
        return registry
    # 收编逐行过 harness.Case（原则 9）：坏行显式报错，不静默带入派生产物
    text = (scen / "cases.jsonl").read_text(encoding="utf-8")
    try:
        for lineno, raw in iter_cases_lines(text, f"{scen.name}/cases.jsonl"):
            Case(raw, f"{scen.name}/cases.jsonl#L{lineno}")
    except ValueError as exc:
        raise ValueError(f"既有 cases.jsonl 无法收编"
                         f"（请先修复或移除该文件）: {exc}") from exc
    _write_text(_script_file(scen, LEGACY_SCRIPT_ID),
                "\n".join(json.dumps(r, ensure_ascii=False) for r in legacy_rows)
                + "\n")
    registry = [{"id": LEGACY_SCRIPT_ID, "name": "既有用例（收编）",
                 "case_type": "迁移", "external_ref": "", "source": "adopt",
                 "created_at": _now(), "updated_at": _now()}]
    _save_registry(scen, registry)
    return registry


def get_scenario(scenario_id: str) -> dict:
    """场景详情：清单摘要 + 受管脚本登记。"""
    summary = next((s for s in scan_scenarios() if s["id"] == scenario_id), None)
    if summary is None:
        raise FileNotFoundError(f"场景不存在: {scenario_id!r}")
    summary["script_entries"] = _load_registry(_user_root() / scenario_id)
    return summary


def delete_scenario(scenario_id: str) -> dict:
    """删除用户场景（整目录：manifest + scripts 登记 + 行文件 + 派生 cases.jsonl）。

    builtin 只读（409 语义）；不存在报 404 语义。历史任务/版本归档持配置快照
    （归档 details 存维度键与分数），不随场景删除失效。
    """
    scen = _scenario_dir(scenario_id)
    summary = _scenario_summary(scen, "user")
    shutil.rmtree(scen)
    return summary


def list_scripts(scenario_id: str | None = None) -> list[dict]:
    """跨场景脚本清单（含 builtin 用例数据 + 用户自建脚本）。"""
    items: list[dict] = []
    for s in scan_scenarios():
        if scenario_id and s["id"] != scenario_id:
            continue
        root = _builtin_root() if s["source"] == "builtin" else _user_root()
        scen = root / s["id"]
        # builtin 场景：cases.jsonl 直接作为脚本内容透出（只读）
        if s["source"] == "builtin":
            cases = _read_rows(scen / "cases.jsonl")
            if cases:
                items.append({
                    "id": s["id"], "name": s.get("display_name", s["id"]),
                    "scenario": s["id"], "scenario_name": s.get("display_name", s["id"]),
                    "source": "builtin", "readonly": True,
                    "row_count": len(cases), "cases": cases,
                    "row_index": [[str(x.get("id")), str(x.get("desc", ""))] for x in cases],
                    "description": s.get("description", ""),
                    "dimension": s.get("dimension", ""),
                })
            continue
        try:
            registry = _load_registry(scen)
        except ValueError as exc:
            logger.warning("跳过损坏的场景登记 %s: %s", s["id"], exc)
            continue
        for entry in registry:
            rows = _read_rows(_script_file(scen, entry["id"]))
            items.append({**entry, "scenario": s["id"],
                          "scenario_name": s["display_name"],
                          "row_count": len(rows),
                          "row_index": [[str(r.get("id")), str(r.get("desc", ""))] for r in rows]})
    return sorted(items, key=lambda i: (i["scenario"], i["id"]))


def _find_script(sid: str) -> tuple[Path, dict]:
    for s in scan_scenarios(source="user"):
        scen = _user_root() / s["id"]
        for entry in _load_registry(scen):
            if entry["id"] == sid:
                return scen, entry
    raise FileNotFoundError(f"脚本不存在: {sid!r}")


def _assert_free_id(sid: str) -> None:
    if not SCRIPT_ID_RE.match(sid or ""):
        raise ValueError(f"非法脚本 id: {sid!r}（小写字母开头，允许数字/-/_，2-64 位）")
    for s in scan_scenarios(source="user"):
        if any(e["id"] == sid for e in _load_registry(_user_root() / s["id"])):
            raise FileExistsError(f"脚本 id 已存在: {sid!r}（全平台唯一）")


def create_script(*, scenario_id: str, script_id: str, name: str,
                  case_type: str = "基础", external_ref: str = "",
                  rows: list[dict], source: str = "manual") -> dict:
    """新建脚本：行级校验 → scripts/<id>.jsonl + 登记 → 重派生 cases.jsonl。"""
    if case_type not in CASE_TYPES:
        raise ValueError(f"非法用例类型: {case_type!r}（合法：{'/'.join(CASE_TYPES)}）")
    _assert_free_id(script_id)
    scen = _scenario_dir(scenario_id)
    registry = _ensure_managed(scen)
    normalized = _normalize_rows(rows, script_id, _all_row_ids(scen))
    _write_text(_script_file(scen, script_id),
                "\n".join(json.dumps(r, ensure_ascii=False) for r in normalized) + "\n")
    entry = {"id": script_id, "name": name, "case_type": case_type,
             "external_ref": external_ref, "source": source,
             "created_at": _now(), "updated_at": _now()}
    registry.append(entry)
    _save_registry(scen, registry)
    _materialize(scen)
    return {**entry, "scenario": scenario_id, "row_count": len(normalized)}


def get_script(sid: str) -> dict:
    scen, entry = _find_script(sid)
    return {**entry, "scenario": scen.name, "rows": _read_rows(_script_file(scen, sid))}


def update_script(sid: str, *, name: str | None = None,
                  case_type: str | None = None, external_ref: str | None = None,
                  rows: list[dict] | None = None) -> dict:
    """部分更新；rows 提供时整体重写并重派生 cases.jsonl。"""
    if case_type is not None and case_type not in CASE_TYPES:
        raise ValueError(f"非法用例类型: {case_type!r}（合法：{'/'.join(CASE_TYPES)}）")
    scen, entry = _find_script(sid)
    for key, val in (("name", name), ("case_type", case_type),
                     ("external_ref", external_ref)):
        if val is not None:
            entry[key] = val
    if rows is not None:
        normalized = _normalize_rows(rows, sid, _all_row_ids(scen, exclude_sid=sid))
        _write_text(_script_file(scen, sid),
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in normalized) + "\n")
    entry["updated_at"] = _now()
    registry = _load_registry(scen)
    _save_registry(scen, [entry if e["id"] == sid else e for e in registry])
    _materialize(scen)
    return {**entry, "scenario": scen.name,
            "row_count": len(_read_rows(_script_file(scen, sid)))}


def delete_script(sid: str) -> dict:
    scen, _ = _find_script(sid)
    _script_file(scen, sid).unlink(missing_ok=True)
    registry = [e for e in _load_registry(scen) if e["id"] != sid]
    _save_registry(scen, registry)
    _materialize(scen)
    return {"deleted": True, "id": sid, "scenario": scen.name}


async def import_script(repo: Any, *, scenario_id: str, script_id: str,
                        name: str, case_type: str = "基础",
                        external_ref: str = "", jsonl: str = "",
                        dataset: str = "") -> dict:
    """数据集 / 粘贴 jsonl 整体导入为 1 个脚本（同一 harness 校验，不拆散）。"""
    if bool(jsonl) == bool(dataset):
        raise ValueError("jsonl 与 dataset 恰好提供其一")
    if dataset:
        ref = await resolve_dataset_ref(repo, dataset)
        path = Path(ref["path"]) / "cases.jsonl" if Path(ref["path"]).is_dir() \
            else Path(ref["path"])
        text = path.read_text(encoding="utf-8")
    else:
        text = jsonl
    rows = [raw for _, raw in iter_cases_lines(text, script_id)]
    # 覆盖语义：同 id 重导 = 整体重写该脚本；跨场景则迁移（删旧建新）；全新 id 照常创建
    try:
        old_scen, _old_entry = _find_script(script_id)
        overwritten = True
    except FileNotFoundError:
        old_scen, overwritten = None, False
    if overwritten and old_scen.name == scenario_id:
        updated = update_script(script_id, name=name, case_type=case_type,
                                external_ref=external_ref, rows=rows)
        return {**updated, "overwritten": True}
    if overwritten:
        delete_script(script_id)
    created = create_script(scenario_id=scenario_id, script_id=script_id,
                            name=name, case_type=case_type,
                            external_ref=external_ref, rows=rows, source="import")
    return {**created, "overwritten": overwritten}


async def list_dim_tags(repo: Any) -> list[dict]:
    """内置标签 + 自定义标签（DB），按名去重；source 标注来源。"""
    custom = {t["name"]: t for t in await repo.list_dim_tags()}
    items = [{"name": n, "source": "builtin", "id": None}
             for n in BUILTIN_DIM_TAGS if n not in custom]
    items += [{"name": t["name"], "source": "custom", "id": t["id"],
               "created_at": t["created_at"]} for t in custom.values()]
    return items


async def create_dim_tag(repo: Any, name: str) -> dict:
    name = (name or "").strip()
    if not name or len(name) > 32:
        raise ValueError(f"非法标签名: {name!r}（1-32 字）")
    if name in BUILTIN_DIM_TAGS or any(
            t["name"] == name for t in await repo.list_dim_tags()):
        raise FileExistsError(f"标签已存在: {name!r}")
    try:
        return await repo.create_dim_tag(name)
    except IntegrityError as exc:   # 并发双击：唯一约束兜底 → 409 语义
        raise FileExistsError(f"标签已存在: {name!r}") from exc
