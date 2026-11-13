"""功能域评估场景与脚本（用例）资产层（V1-PLAN §10.4/§10.5）。

目录契约（场景全部用户自管，根 ``config.DEFAULT_FUNCTIONAL_SCENARIOS_DIR``，
引擎 pytest_adapter 缺省同源）::

    <root>/<scenario>/manifest.json        # 显示名 / 维度标签 / 说明
    <root>/<scenario>/scripts.json         # 受管脚本登记（name/case_type/external_ref/source）
    <root>/<scenario>/scripts/<sid>.jsonl  # 脚本数据行（harness.Case 同构字典）
    <root>/<scenario>/cases.jsonl          # 派生产物 = 全部脚本行拼接（pytest 引擎零改动消费）

首次向场景写入脚本时若已存在无主 ``cases.jsonl`` → 自动收编为脚本
``legacy-cases``（case_type=迁移），此后 ``cases.jsonl`` 全量由脚本派生。
脚本 id 全平台唯一（跨场景寻址无歧义）。文件写出强制 LF（Windows 宿主
CRLF 教训，见 roadmap 2026-10-11）。
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
from aite.functional.harness import Case
from aite.functional.ingest import iter_cases_lines
from aite.utils.logging import get_logger

logger = get_logger("aite.service.functional_assets")

SCRIPT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SCENARIO_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # OpenAI function name 约束
LEGACY_SCRIPT_ID = "legacy-cases"
BUILTIN_DIM_TAGS = ("API 契约", "模型能力", "Agent")
CASE_TYPES = ("基础", "边界", "异常", "兼容", "迁移")
ROW_TYPES = ("基础", "边界", "异常", "兼容", "bad case")
ROW_LEVELS = ("P0", "P1", "P2", "P3")
_ROW_META_KEYS = ("case_id", "type", "level")

# 命名约定：id = 英文编号（机器寻址：目录名 / jsonl 路径 / tools.cases 前缀），
# name / display_name = 中文名称（人看：卡片标题、大屏维度行名、报告结论）。
# 分层：场景 → 脚本（1 jsonl 文件）→ 行（= 执行原子，行 id 场景内唯一）；
# 行可挂 case_id（外部用例平台编号，多行可同挂一个用例）+ type 类型 + level 等级。


class ScenarioNotEmptyError(FileExistsError):
    """场景下仍有脚本/用例时未带 force 删除（继承 FileExistsError → 409 同映射）。"""


def _user_root() -> Path:
    return Path(config.DEFAULT_FUNCTIONAL_SCENARIOS_DIR)


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


def _scenario_summary(path: Path) -> dict:
    manifest = _load_manifest(path)
    registry = _load_registry(path)
    coverage: dict[str, int] = {}
    levels: dict[str, int] = {}
    for entry in registry:
        for row in _read_rows(_script_file(path, entry["id"])):
            kind = row.get("type") or entry.get("case_type", "未分类")   # 行级优先
            coverage[kind] = coverage.get(kind, 0) + 1
            if row.get("level"):
                levels[row["level"]] = levels.get(row["level"], 0) + 1
    return {"id": path.name, **manifest,
            "forms": "script" if any(
                p.name.startswith("test_") and p.name.endswith(".py")
                for p in path.iterdir()) else "jsonl",
            "scripts": len(registry),
            "cases": _count_cases(path), "coverage": coverage, "levels": levels}


def scan_scenarios() -> list[dict]:
    """托管场景根的场景清单；损坏目录跳过 + 告警，不拖垮整个列表。"""
    root = _user_root()
    items: list[dict] = []
    if not root.is_dir():
        return items
    for path in sorted(root.iterdir()):
        if not _is_scenario_dir(path):
            continue
        try:
            items.append(_scenario_summary(path))
        except ValueError as exc:
            logger.warning("跳过损坏的场景目录 %s: %s", path, exc)
    return sorted(items, key=lambda i: i["id"])


async def create_scenario(repo: Any, sid: str, display_name: str = "",
                          dimension: str = "", description: str = "") -> dict:
    """新建用户场景：id = 英文编号，display_name = 中文名称（维度标签须在标签库内）。"""
    if not SCENARIO_ID_RE.match(sid or ""):
        raise ValueError(f"非法场景 id: {sid!r}"
                         f"（英文编号：小写字母开头，允许数字/-/_，2-64 位）")
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
    if target.exists():
        raise FileExistsError(f"场景 {sid!r} 已存在")
    manifest = {"display_name": display_name or sid, "dimension": dimension,
                "description": description}
    _write_text(target / "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2))
    return _scenario_summary(target)


def _scenario_dir(scenario_id: str) -> Path:
    """托管场景目录解析；不存在报 404 语义。"""
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


def _check_row_meta(row: dict, where: str) -> None:
    """用例级标签校验（引擎透传不消费）：空值剔除，非空须在枚举内。"""
    for key in _ROW_META_KEYS:
        if row.get(key) in ("", None):
            row.pop(key, None)
    if "type" in row and row["type"] not in ROW_TYPES:
        raise ValueError(f"{where}: 非法用例类型 {row['type']!r}（合法：{'/'.join(ROW_TYPES)}）")
    if "level" in row and row["level"] not in ROW_LEVELS:
        raise ValueError(f"{where}: 非法等级 {row['level']!r}（合法：{'/'.join(ROW_LEVELS)}）")
    if "case_id" in row and (not isinstance(row["case_id"], str) or len(row["case_id"]) > 128):
        raise ValueError(f"{where}: 用例 id 须为 ≤128 字的字符串")
    if "tools" in row and not isinstance(row["tools"], list):
        raise ValueError(f"{where}: tools 须为数组（工具库工具名 / 完整工具定义）")


def _normalize_rows(rows: list[dict], sid: str,
                    taken: set[str]) -> list[dict]:
    """行级校验 + 场景内唯一 id 分配（显式 id 优先，缺省 sid.n）。

    tools 里的字符串 = 工具库引用：保存时校验存在（未知工具显式报错），
    行文件保留引用名，派生 cases.jsonl 时展开为完整定义。
    """
    normalized: list[dict] = []
    lib: dict[str, dict] | None = None
    for i, raw in enumerate(rows, start=1):
        rid = str(raw.get("id") or f"{sid}.{i}")
        if rid in taken:
            raise ValueError(f"用例 id 在场景内冲突: {rid!r}")
        data = {**raw, "id": rid}                  # 先补 id（Case 必填）再校验
        where = f"script:{sid}#row{i}"
        _check_row_meta(data, where)
        if _has_tool_refs(data):
            lib = _load_tools() if lib is None else lib
            _resolve_tools(data["tools"], lib, where)
        Case(data, where)                          # 校验失败抛 ValueError
        taken.add(rid)
        normalized.append(data)
    if not normalized:
        raise ValueError("脚本至少需要 1 行数据")
    return normalized


def _materialize(scen: Path) -> None:
    """cases.jsonl = 全部受管脚本行拼接，工具库引用展开为完整定义（引擎契约零改动）。"""
    lines: list[str] = []
    lib: dict[str, dict] | None = None
    for entry in _load_registry(scen):
        for row in _read_rows(_script_file(scen, entry["id"])):
            if _has_tool_refs(row):
                lib = _load_tools() if lib is None else lib
                row = {**row, "tools": _resolve_tools(
                    row["tools"], lib, f"{scen.name}/{entry['id']}#{row.get('id')}")}
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


def delete_scenario(scenario_id: str, force: bool = False) -> dict:
    """删除场景。

    关联语义：脚本绑定场景（脚本目录/登记物理内嵌于场景目录，无法脱离场景
    存在）。默认**有内容拒删**（脚本或用例行 > 0 → ScenarioNotEmptyError），
    防误删手工用例资产与外部用例映射（external_ref）；``force=True`` 显式
    级联删除整目录（manifest + scripts 登记 + 行文件 + 派生 cases.jsonl）。
    空场景（仅 manifest）直接删除。历史任务/版本归档持配置快照（归档
    details 存维度键与分数），不随场景删除失效。
    """
    scen = _scenario_dir(scenario_id)
    summary = _scenario_summary(scen)
    if not force and (summary["scripts"] or summary["cases"]):
        raise ScenarioNotEmptyError(
            f"场景 {scenario_id!r} 下仍有 {summary['scripts']} 个脚本 / "
            f"{summary['cases']} 条用例：请先删除脚本，或确认后级联删除"
            f"（force=true）")
    shutil.rmtree(scen)
    return summary


def list_scripts(scenario_id: str | None = None) -> list[dict]:
    """跨场景脚本清单。"""
    items: list[dict] = []
    for s in scan_scenarios():
        if scenario_id and s["id"] != scenario_id:
            continue
        scen = _user_root() / s["id"]
        try:
            registry = _load_registry(scen)
        except ValueError as exc:
            logger.warning("跳过损坏的场景登记 %s: %s", s["id"], exc)
            continue
        for entry in registry:
            rows = _read_rows(_script_file(scen, entry["id"]))
            row_types: dict[str, int] = {}
            row_levels: dict[str, int] = {}
            for r in rows:
                kind = r.get("type") or entry.get("case_type", "未分类")
                row_types[kind] = row_types.get(kind, 0) + 1
                if r.get("level"):
                    row_levels[r["level"]] = row_levels.get(r["level"], 0) + 1
            items.append({**entry, "scenario": s["id"],
                          "scenario_name": s["display_name"],
                          "row_count": len(rows),
                          "row_types": row_types, "row_levels": row_levels,
                          "row_index": [[str(r.get("id")), str(r.get("desc", ""))] for r in rows]})
    return sorted(items, key=lambda i: (i["scenario"], i["id"]))


def _find_script(sid: str) -> tuple[Path, dict]:
    for s in scan_scenarios():
        scen = _user_root() / s["id"]
        for entry in _load_registry(scen):
            if entry["id"] == sid:
                return scen, entry
    raise FileNotFoundError(f"脚本不存在: {sid!r}")


def _assert_free_id(sid: str) -> None:
    if not SCRIPT_ID_RE.match(sid or ""):
        raise ValueError(f"非法脚本 id: {sid!r}"
                         f"（英文编号：小写字母开头，允许数字/-/_，2-64 位）")
    for s in scan_scenarios():
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


# ------------------------------------------------------------ 工具库（tool_calls）
# 存储 <托管根上级>/tools.json：OpenAI tools 条目数组（按 function.name 寻址）。
# 数据行 tools 写工具名字符串 = 引用；派生 cases.jsonl 时展开，引擎看到完整定义。

def _tools_file() -> Path:
    return _user_root().parent / "tools.json"


def _load_tools() -> dict[str, dict]:
    return {t["function"]["name"]: t for t in _read_json(_tools_file(), [])}


def _save_tools(lib: dict[str, dict]) -> None:
    _write_text(_tools_file(), json.dumps(
        [lib[n] for n in sorted(lib)], ensure_ascii=False, indent=2))


def _has_tool_refs(row: dict) -> bool:
    return any(isinstance(t, str) for t in row.get("tools") or [])


def _resolve_tools(tools: list, lib: dict[str, dict], where: str) -> list:
    out = []
    for t in tools:
        if isinstance(t, str):
            if t not in lib:
                raise ValueError(f"{where}: 未知工具 {t!r}（工具库无此工具，先到工具库新建）")
            out.append(lib[t])
        else:
            out.append(t)
    return out


def _validate_tool(schema: Any) -> str:
    """OpenAI tools 条目：{type: function, function: {name, description?, parameters?}}。"""
    if not isinstance(schema, dict) or schema.get("type") != "function" \
            or not isinstance(schema.get("function"), dict):
        raise ValueError('工具定义须为 OpenAI tools 条目：{"type": "function", "function": {...}}')
    fn = schema["function"]
    name = fn.get("name")
    if not isinstance(name, str) or not TOOL_NAME_RE.match(name):
        raise ValueError(f"非法工具名 {name!r}（字母/数字/_/-，1-64 位）")
    params = fn.get("parameters")
    if params is not None and (not isinstance(params, dict) or params.get("type") != "object"):
        raise ValueError(f"工具 {name}：parameters 须为 JSON Schema 对象（type: object）")
    return name


def _tool_usage() -> dict[str, list[str]]:
    """工具名 → 引用它的脚本 id（删除保护 / 清单展示 / 改动后重派生）。"""
    usage: dict[str, list[str]] = {}
    for s in scan_scenarios():
        scen = _user_root() / s["id"]
        for entry in _load_registry(scen):
            for row in _read_rows(_script_file(scen, entry["id"])):
                for t in row.get("tools") or []:
                    if isinstance(t, str) and entry["id"] not in usage.setdefault(t, []):
                        usage[t].append(entry["id"])
    return usage


def list_tools() -> list[dict]:
    usage = _tool_usage()
    return [{"name": n, "description": t["function"].get("description", ""),
             "schema": t, "used_by": usage.get(n, [])}
            for n, t in sorted(_load_tools().items())]


def create_tool(schema: dict) -> dict:
    name = _validate_tool(schema)
    lib = _load_tools()
    if name in lib:
        raise FileExistsError(f"工具已存在: {name!r}")
    lib[name] = schema
    _save_tools(lib)
    return {"name": name, "schema": schema, "used_by": []}


def update_tool(name: str, schema: dict) -> dict:
    """整体替换定义（不支持改名：工具名是数据行的引用键）；引用场景重派生。"""
    lib = _load_tools()
    if name not in lib:
        raise FileNotFoundError(f"工具不存在: {name!r}")
    if _validate_tool(schema) != name:
        raise ValueError(f"不支持改名（工具名 {name!r} 是数据行引用键）：请新建工具后改引用")
    lib[name] = schema
    _save_tools(lib)
    used = _tool_usage().get(name, [])
    for scen in {_find_script(sid)[0] for sid in used}:
        _materialize(scen)
    return {"name": name, "schema": schema, "used_by": used}


def delete_tool(name: str) -> dict:
    lib = _load_tools()
    if name not in lib:
        raise FileNotFoundError(f"工具不存在: {name!r}")
    used = _tool_usage().get(name, [])
    if used:
        raise FileExistsError(f"工具 {name!r} 仍被 {len(used)} 个脚本引用"
                              f"（{', '.join(used)}）：先移除引用再删除")
    del lib[name]
    _save_tools(lib)
    return {"deleted": True, "name": name}


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
