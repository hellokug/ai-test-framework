"""精度域评估场景与脚本资产层（V1-PLAN §10.4 accuracy 形态）。

脚本 = 固化的域任务 spec + 名字（spec = suite raw 同构，与
``POST /accuracy/tasks`` 的 spec 完全同形；YAML 只是序列化形态）。
场景与脚本**全部用户自管，无预置**（研发不随框架发脚本，用户经
REST/CLI 自建；spec 保存经 resolve_suite 真实校验——工具域轻校验 /
自研域 SuiteConfig 强校验；运行参数不沉淀，发起任务时覆盖）。
目录契约（与功能域同构，见 functional_assets）::

    <config.DEFAULT_ACCURACY_SCENARIOS_DIR>/<scenario>/manifest.json
        # {display_name, dimension, description}
    <…>/<scenario>/scripts/<sid>.yaml
        # 脚本 doc = {id, name, description, spec, created_at, updated_at}

命名约定与功能域一致：id = 英文编号（机器寻址：文件名 / 跨场景寻址），
name = 中文名称（人看：脚本库卡片标题）。脚本 id 全平台唯一。
清单瘦身口径：列表项只带 数据集 + 条数 + 更新时间，不带描述/spec（详情
接口才有）。条数 = 数据集规模合计：自研域可本地落定（catalog 登记 /
builtin 打包样本 / 本地文件行数）；工具域（engine=evalscope）基准全量在
evalscope 侧，本地不可知 → None（前端显示 —）。文件写出强制 LF
（Windows 宿主 CRLF 教训，见 roadmap 2026-10-11）。
"""

from __future__ import annotations

import asyncio
import copy
import json
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
SCENARIO_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class ScenarioNotEmptyError(FileExistsError):
    """场景下仍有脚本时未带 force 删除（继承 FileExistsError → 409 同映射）。"""


def _scenarios_root() -> Path:
    return Path(config.DEFAULT_ACCURACY_SCENARIOS_DIR)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()   # 3.10 兼容（datetime.UTC 是 3.11+）


def _write_text(path: Path, text: str) -> None:
    """原子写出（tmp + os.replace）：崩溃不留半截脚本文件。"""
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


# ---------------------------------------------------------------- 场景


def _is_scenario_dir(path: Path) -> bool:
    if not path.is_dir() or path.name.startswith(("_", ".")):
        return False
    return (path / "manifest.json").is_file() or (path / "scripts").is_dir()


def _load_manifest(scen: Path) -> dict:
    manifest = _read_json(scen / "manifest.json", {})
    return {"display_name": manifest.get("display_name") or scen.name,
            "dimension": manifest.get("dimension", ""),
            "description": manifest.get("description", "")}


def _scenario_dirs() -> list[Path]:
    """托管场景根下的合法场景目录；损坏目录跳过 + 告警，不拖垮整个列表。"""
    root = _scenarios_root()
    if not root.is_dir():
        return []
    return [p for p in sorted(root.iterdir()) if _is_scenario_dir(p)]


def _scenario_dir(scenario_id: str) -> Path:
    scen = _scenarios_root() / scenario_id
    if not _is_scenario_dir(scen):
        raise FileNotFoundError(f"场景不存在: {scenario_id!r}")
    return scen


def _script_files(scen: Path) -> list[Path]:
    return sorted((scen / "scripts").glob("*.yaml")) \
        if (scen / "scripts").is_dir() else []


# ------------------------------------------------------- 数据集与条数口径


def _dataset_refs(spec: dict) -> list[Any]:
    """spec 里的数据集引用原样收集：工具域 tools.datasets（str）/ 自研域
    datasets（str|dict 同构 DatasetConfig）。"""
    refs: list[Any] = []
    for source in ((spec.get("tools") or {}).get("datasets"),
                   spec.get("datasets")):
        if isinstance(source, list):
            refs.extend(source)
    return refs


def _ref_label(ref: Any) -> str:
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict):
        return str(ref.get("id") or ref.get("ref") or ref.get("path")
                   or ref.get("type", "?"))
    return str(ref)


def _count_file_lines(path: Path) -> int | None:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                   if line.strip())
    except OSError:
        return None


async def _resolve_ref_count(repo: Any, ref: Any, allow_packaged: bool) -> int | None:
    """单个数据集引用的条数：catalog 登记 → 本地文件 → builtin 打包样本。

    allow_packaged=False（工具域，engine=evalscope）：tools.datasets 是
    evalscope 自己的基准名，跑的是上游全量而非打包抽样，本地不可知 → 只认
    catalog 显式登记（add-hf 导入），否则 None。
    """
    from aite.datasets import builtin
    from aite.datasets.catalog import resolve_dataset_ref

    label = None
    if isinstance(ref, str):
        label = ref
    elif isinstance(ref, dict):
        label = ref.get("ref") or ref.get("id")
    if label:
        try:
            row = await resolve_dataset_ref(repo, label)
        except ValueError:
            row = None
        if row:
            count = (row.get("stats") or {}).get("count")
            if isinstance(count, int):
                return count
            n = _count_file_lines(Path(row["path"]))
            if n is not None:
                return n
    if isinstance(ref, dict):
        path = ref.get("path")
        if path:
            return _count_file_lines(Path(path))
    if allow_packaged:
        entry = builtin.merged_info().get(_ref_label(ref))
        if entry is not None:
            try:
                return len(builtin._read_entry(entry))
            except (OSError, ValueError):
                return None
    return None


async def script_metrics(repo: Any, spec: dict) -> tuple[list[str], int | None]:
    """spec → (数据集标签列表, 条数合计)；任一引用条数不可落定 → None。"""
    refs = _dataset_refs(spec)
    allow_packaged = not spec.get("engine")   # 自研域才真正加载打包样本
    counts = [await _resolve_ref_count(repo, r, allow_packaged) for r in refs]
    total = None if any(c is None for c in counts) else sum(counts)
    return [_ref_label(r) for r in refs], total


# ---------------------------------------------------------------- 脚本


def _script_path(scen: Path, sid: str) -> Path:
    return scen / "scripts" / f"{sid}.yaml"


def _load_script_doc(path: Path) -> dict | None:
    """脚本 YAML → doc；损坏文件返回 None（服务端告警，前端无感）。"""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or "spec" not in doc:
            raise ValueError("缺 spec 字段")
    except Exception as exc:   # noqa: BLE001 - 单文件损坏不推翻整个脚本库
        logger.warning("精度脚本 %s 读取失败，已跳过: %s", path.name, exc)
        return None
    doc.setdefault("id", path.stem)
    return doc


def _list_item(scen: Path, doc: dict, metrics: tuple[list[str], int | None]) -> dict:
    return {"id": doc["id"], "name": doc.get("name") or doc["id"],
            "scenario": scen.name, "scenario_name": _load_manifest(scen)["display_name"],
            "datasets": metrics[0], "case_count": metrics[1],
            "updated_at": doc.get("updated_at")}


async def list_scripts(repo: Any, scenario_id: str | None = None) -> list[dict]:
    """跨场景脚本清单（瘦身口径：数据集 + 条数，无描述/spec）。"""
    items: list[dict] = []
    for scen in _scenario_dirs():
        if scenario_id and scen.name != scenario_id:
            continue
        for path in _script_files(scen):
            doc = _load_script_doc(path)
            if doc is not None:
                items.append(_list_item(scen, doc,
                                        await script_metrics(repo, doc["spec"])))
    return sorted(items, key=lambda i: (i["scenario"], i["id"]))


def _find_script(sid: str) -> tuple[Path, dict]:
    for scen in _scenario_dirs():
        path = _script_path(scen, sid)
        if path.is_file():
            doc = _load_script_doc(path)
            if doc is not None:
                return scen, doc
    raise FileNotFoundError(f"脚本不存在: {sid!r}")


def _assert_free_id(sid: str) -> None:
    if not SCRIPT_ID_RE.match(sid or ""):
        raise ValueError(f"非法脚本 id: {sid!r}"
                         f"（英文编号：小写字母开头，允许数字/-/_，2-64 位）")
    for scen in _scenario_dirs():
        if _script_path(scen, sid).is_file():
            raise FileExistsError(f"脚本 id 已存在: {sid!r}（全平台唯一）")


def _validate_spec(spec: Any) -> dict:
    """保存前真实校验：数据集非空 + resolve_suite（工具域轻 / 自研域强）。

    校验跑在深拷贝上：resolve_suite 入口的 apply_common 会就地展开
    target.profile 并并入 common 工具缺省（如本机 python_exe），直接校验原件
    会把展开结果固化进脚本文件（profile 引用失效 + 本机路径泄漏）。
    """
    if not isinstance(spec, dict):
        raise ValueError("spec 须为对象（suite raw 同构）")
    if not _dataset_refs(spec):
        raise ValueError("评测数据集不能为空（datasets / tools.datasets）")
    from aite.service.run_service import resolve_suite

    try:
        resolve_suite(copy.deepcopy(spec))
    except ValueError as exc:
        raise ValueError(f"spec 校验失败：{exc}") from exc
    return spec


async def create_script(repo: Any, *, scenario_id: str, script_id: str,
                        name: str, description: str = "", spec: dict) -> dict:
    """新建脚本：id 规则 + 全平台唯一 + spec 真实校验 → YAML 落盘。"""
    _assert_free_id(script_id)
    scen = _scenario_dir(scenario_id)
    _validate_spec(spec)
    doc = {"id": script_id, "name": name or script_id, "description": description,
           "spec": spec, "created_at": _now(), "updated_at": _now()}
    await asyncio.to_thread(
        _write_text, _script_path(scen, script_id),
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    return await get_script(repo, script_id)


async def get_script(repo: Any, sid: str) -> dict:
    scen, doc = await asyncio.to_thread(_find_script, sid)
    datasets, case_count = await script_metrics(repo, doc["spec"])
    return {**doc, "scenario": scen.name,
            "scenario_name": _load_manifest(scen)["display_name"],
            "datasets": datasets, "case_count": case_count}


async def update_script(repo: Any, sid: str, *, name: str | None = None,
                        description: str | None = None,
                        spec: dict | None = None) -> dict:
    """部分更新（编辑即生效；历史任务持创建时快照不受影响）。"""
    scen, doc = await asyncio.to_thread(_find_script, sid)
    if spec is not None:
        _validate_spec(spec)
        doc["spec"] = spec
    for key, val in (("name", name), ("description", description)):
        if val is not None:
            doc[key] = val
    doc["updated_at"] = _now()
    await asyncio.to_thread(
        _write_text, _script_path(scen, sid),
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    return await get_script(repo, sid)


async def delete_script(sid: str) -> dict:
    scen, doc = await asyncio.to_thread(_find_script, sid)
    await asyncio.to_thread(_script_path(scen, sid).unlink)
    return {"deleted": True, "id": sid, "scenario": scen.name,
            "name": doc.get("name") or sid}


# ---------------------------------------------------------------- 场景 CRUD


async def create_scenario(repo: Any, sid: str, display_name: str = "",
                          dimension: str = "", description: str = "") -> dict:
    """新建用户场景：id = 英文编号，display_name = 中文名称（维度标签须在共享标签库内）。"""
    if not SCENARIO_ID_RE.match(sid or ""):
        raise ValueError(f"非法场景 id: {sid!r}"
                         f"（英文编号：小写字母开头，允许数字/-/_，2-64 位）")
    if dimension:
        from aite.service.functional_assets import list_dim_tags

        allowed = {t["name"] for t in await list_dim_tags(repo)}
        if dimension not in allowed:
            raise ValueError(f"未知维度标签: {dimension!r}"
                             f"（合法：{'/'.join(sorted(allowed))}；可先到标签库新增）")
    target = _scenarios_root() / sid
    if target.exists():
        raise FileExistsError(f"场景 {sid!r} 已存在")
    manifest = {"display_name": display_name or sid, "dimension": dimension,
                "description": description}
    await asyncio.to_thread(
        _write_text, target / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2))
    return await scenario_summary(repo, sid)


async def scenario_summary(repo: Any, scenario_id: str) -> dict:
    scen = _scenario_dir(scenario_id)
    docs = [d for d in (_load_script_doc(p) for p in _script_files(scen))
            if d is not None]
    counts = [await script_metrics(repo, d["spec"]) for d in docs]
    cases = None if any(m[1] is None for m in counts) \
        else sum(m[1] for m in counts)
    return {"id": scen.name, **_load_manifest(scen), "scripts": len(docs),
            "cases": cases}


async def scan_scenarios(repo: Any) -> list[dict]:
    items = [await scenario_summary(repo, p.name) for p in _scenario_dirs()]
    return sorted(items, key=lambda i: i["id"])


async def get_scenario(repo: Any, scenario_id: str) -> dict:
    summary = await scenario_summary(repo, scenario_id)
    items = await list_scripts(repo, scenario_id)
    summary["script_entries"] = [
        {k: v for k, v in i.items() if k != "scenario_name"} for i in items]
    return summary


async def delete_scenario(scenario_id: str, force: bool = False) -> dict:
    """删除场景。脚本物理内嵌于场景目录（scripts/<sid>.yaml），默认**有脚本
    拒删**（ScenarioNotEmptyError），``force=True`` 显式级联删除整目录。
    历史任务/版本归档持配置快照（spec 内嵌），不随场景删除失效。"""
    import shutil

    scen = _scenario_dir(scenario_id)
    manifest = _load_manifest(scen)
    scripts = len(_script_files(scen))
    if not force and scripts:
        raise ScenarioNotEmptyError(
            f"场景 {scenario_id!r} 下仍有 {scripts} 个脚本：请先删除脚本，"
            f"或确认后级联删除（force=true）")
    await asyncio.to_thread(shutil.rmtree, scen)
    return {"id": scenario_id, **manifest, "scripts": scripts}
