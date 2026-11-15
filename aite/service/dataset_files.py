"""数据集文件域服务：上传 / 下载 / 在线导入（HF 镜像、OBS 等远程拉取）。

在统一 catalog（datasets/catalog）之上补文件面（全部落到数据集设置
``settings.py`` 收口的目录）：
- 上传：文本格式（jsonl/json/csv）→ 分类型校验 → 落位 → 登记（source=upload）；
- 下载：catalog 引用 → 本地文件 / 包内置精度样本 → 文件响应；
- HF 导入：``add_hf`` 管线（镜像端点/缓存目录由设置注入）+ catalog 登记；
- 远程导入：OBS 桶 / 任意 http 直链 → 复用上传校验管线（source=remote）；
- 探测：HF 镜像与远程地址连通性（前端「和 HF 通/不通」决策依据）。

阻塞的网络/下载调用经 run_in_threadpool 下放，不卡事件循环。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
from fastapi.concurrency import run_in_threadpool

from aite.datasets import settings as ds_settings
from aite.datasets import userstore
from aite.datasets.catalog import (
    check_type,
    ownership_from_source,
    register_entry,
    resolve_dataset_ref,
    tags_to_capabilities,
)

_TEXT_SUFFIXES = (".jsonl", ".ndjson", ".json", ".csv")
# perf 导入顺手统计真实输出长度的参考列（avg_output_chars，选 max_tokens 参考）。
_PERF_OUTPUT_KEYS = ("expected", "answer", "gold", "output", "label", "target",
                     "response", "completion")
_PROBE_TIMEOUT_S = 4.0
_PULL_TIMEOUT_S = 60.0


def _check_name(name: str) -> None:
    """与 ingest_functional 同规则：非空、无路径分隔符、非相对特指。"""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError(f"非法数据集名: {name!r}")


def _validate_accuracy_records(records: list[dict]) -> dict:
    """精度题库最小校验：非空 + 每条有输入字段（answer/expected 可缺省）。"""
    from aite.datasets.local import _INPUT_KEYS

    if not records:
        raise ValueError("没有可用记录（空数据集）")
    for i, rec in enumerate(records, start=1):
        if not isinstance(rec, dict) or not any(k in rec for k in _INPUT_KEYS):
            raise ValueError(
                f"第 {i} 条记录缺输入字段（{'/'.join(_INPUT_KEYS)} 之一），"
                "精度题库至少要有 question/input 之类的输入列")
    return {"count": len(records)}


def _parse_text_records(filename: str, text: str) -> list[dict]:
    """按上传文件名后缀解析记录（jsonl/json/csv），复用 local 加载器。"""
    from aite.datasets.local import _read_records

    suffix = Path(filename).suffix.lower() or ".jsonl"
    if suffix not in _TEXT_SUFFIXES:
        raise ValueError(
            f"不支持的格式 {suffix}（支持 {'/'.join(_TEXT_SUFFIXES)}；"
            "上传内容须为文本）")
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        tmp = Path(f.name)
    try:
        return _read_records(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _write_canonical_jsonl(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def _import_perf_records(repo: Any, *, name: str, filename: str,
                               records: list[dict], notes: str,
                               source: str,
                               capabilities: list[str] | None = None,
                               ownership: str | None = None) -> dict:
    """真实负载记录 → perf 型 canonical jsonl（input 字段）+ 登记。

    每条记录取扁平输入字段（local._INPUT_KEYS，与 perf_common.INPUT_KEYS
    同集合，保证两引擎 export_prompts 必可消费），缺了取 ShareGPT 对话数组
    的 human 话轮拼接；expected 族列有值时顺手统计 avg_output_chars（选
    max_tokens 参考）。产物配同名 .stats.json——GET /perf/datasets 清单按
    stats.json 枚举，导入产物与生成产物同列表可见。
    """
    from aite.datasets.local import _INPUT_KEYS, human_prompt

    prompts: list[str] = []
    output_chars: list[int] = []
    for i, rec in enumerate(records, start=1):
        if not isinstance(rec, dict):
            raise ValueError(f"第 {i} 条记录须为对象，收到 {type(rec).__name__}")
        text = next((rec[k] for k in _INPUT_KEYS
                     if isinstance(rec.get(k), str) and rec[k].strip()), None)
        if not text:
            text = human_prompt(rec)
        if not text:
            raise ValueError(f"第 {i} 条记录无输入文本"
                             f"（{'/'.join(_INPUT_KEYS)} 之一或 ShareGPT 对话数组）")
        prompts.append(text)
        ref = next((rec[k] for k in _PERF_OUTPUT_KEYS
                    if isinstance(rec.get(k), str) and rec[k].strip()), None)
        if ref:
            output_chars.append(len(ref))
    stats: dict = {"count": len(prompts),
                   "avg_chars": round(sum(map(len, prompts)) / len(prompts), 1)}
    if output_chars:
        stats["avg_output_chars"] = round(sum(output_chars) / len(output_chars), 1)
    path = userstore.dataset_dir("perf") / f"{name}.jsonl"
    _write_canonical_jsonl([{"input": p} for p in prompts], path)
    stats_path = path.with_name(f"{name}.stats.json")
    stats_path.write_text(json.dumps({**stats, "source": source},
                                     ensure_ascii=False), encoding="utf-8")
    return await register_entry(
        repo, dataset_type="perf", name=name, source=source,
        path=str(path).replace("\\", "/"), version="", stats=stats,
        notes=notes or f"upload:{filename}", capabilities=capabilities,
        ownership=ownership)


async def upload_dataset(repo: Any, *, dataset_type: str, name: str,
                         filename: str, content: str | bytes,
                         notes: str = "", force: bool = False,
                         source: str = "upload",
                         capabilities: list[str] | None = None,
                         ownership: str | None = None) -> dict:
    """上传数据集：分类型校验 → 落位数据集目录 → 登记 catalog。

    functional 走 ingest_functional 真实校验器（cases.jsonl 文本）；
    accuracy 解析为记录后规范化成 jsonl 落位（同步外部注册表供 builtin
    加载器消费）；perf 解析真实负载（csv/jsonl，ShareGPT 对话数组取
    human 话轮）规范化成 input-only jsonl 落 perf 目录，场景生成产物
    仍走 POST /perf/datasets。远程拉取复用本函数（source="remote" 区分）。
    capabilities（能力维度）仅 accuracy/perf 适用，归一后直通 catalog 登记。
    """
    check_type(dataset_type)
    _check_name(name)
    existing = await repo.find_dataset_catalog(dataset_type, name)
    if existing and not force:
        raise ValueError(f"{dataset_type}/{name} 已存在（覆盖请传 force=true）")
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content

    if dataset_type == "functional":
        if capabilities or ownership:
            raise ValueError("功能 cases 数据集不适用能力维度/来源标注（仅 accuracy/perf）")
        from aite.functional.ingest import ingest_functional

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8") as f:
            f.write(text)
            tmp = Path(f.name)
        try:
            return await ingest_functional(repo, tmp, name, notes=notes,
                                           force=force)
        finally:
            tmp.unlink(missing_ok=True)

    if dataset_type == "perf":
        return await _import_perf_records(
            repo, name=name, filename=filename,
            records=_parse_text_records(filename, text),
            notes=notes, source=source, capabilities=capabilities,
            ownership=ownership)

    records = _parse_text_records(filename, text)
    stats = _validate_accuracy_records(records)
    path = userstore.dataset_dir("accuracy") / f"{name}.jsonl"
    _write_canonical_jsonl(records, path)
    userstore.upsert(name, {
        "file": f"datasets/accuracy/{name}.jsonl",
        "external": True,
        "upstream": f"upload:{filename}",
        "n": stats["count"],
    })
    return await register_entry(
        repo, dataset_type="accuracy", name=name, source=source,
        path=str(path).replace("\\", "/"), version="", stats=stats,
        notes=notes, capabilities=capabilities, ownership=ownership)


async def resolve_download(repo: Any, ref: str) -> dict:
    """解析下载内容：catalog 登记路径的本地文件优先，回退外部注册表 / 包内置样本。

    ref 不在 catalog 时仍查 builtin 注册表（内置采样题库与外部已下载题库均可
    直下）；跨类型重名的引用冲突不回退（显式报错优先，原则 9）。
    """
    try:
        row = await resolve_dataset_ref(repo, ref)
    except ValueError as exc:
        if "跨类型重名" in str(exc):
            raise
        row = {"type": "accuracy", "name": ref, "path": "", "notes": ""}
        # 目录里未登记的孤儿文件：引用即文件名，直接探测同名 jsonl
        cand = userstore.dataset_dir("accuracy") / f"{ref}.jsonl"
        if cand.is_file():
            row["path"] = str(cand)
    path = Path(row["path"]) if row.get("path") else None
    if path and path.is_file():
        return {"row": row, "kind": "file", "path": path, "filename": path.name}
    if row["type"] == "accuracy":
        from aite.datasets import builtin

        info = builtin.merged_info().get(row["name"])
        if info and info.get("external"):
            # 外部条目 file 相对数据集根（早期登记的 catalog path 可能已失效）
            cand = userstore.data_root() / info["file"]
            if cand.is_file():
                return {"row": row, "kind": "file", "path": cand,
                        "filename": cand.name}
        if info and not info.get("external"):
            trav = resources.files("aite.datasets.builtin") / "data" / info["file"]
            if trav.is_file():
                return {"row": row, "kind": "bytes",
                        "content": trav.read_bytes(), "filename": info["file"]}
    raise LookupError(
        f"数据集 {ref} 没有可下载的本地文件"
        f"（登记 path={row.get('path') or '—'}；在线引用或已删除文件不可下载）")


def _http_get(url: str, timeout: float = _PULL_TIMEOUT_S) -> bytes:
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _parse_fields(fields: Any) -> dict[str, str] | None:
    """字段映射入参收口：dict 原样；"input=question,expected=answer" 解析。"""
    if fields is None or isinstance(fields, dict):
        return fields
    if isinstance(fields, str):
        out = {}
        for pair in fields.split(","):
            k, _, v = pair.partition("=")
            if not k.strip() or not v.strip():
                raise ValueError(f"字段映射格式非法: {pair!r}（应如 input=question）")
            out[k.strip()] = v.strip()
        return out
    raise ValueError(f"fields 须为 dict 或 'k=v,k=v' 字符串，收到 {type(fields).__name__}")


async def hf_import(repo: Any, *, hf_id: str, name: str | None = None,
                    split: str = "test", subset: str | None = None,
                    n: int | None = None, seed: int = 42,
                    fields: Any = None, notes: str = "",
                    dataset_type: str = "accuracy",
                    capabilities: list[str] | None = None,
                    ownership: str | None = None) -> dict:
    """HF 镜像仓导入（add_hf 管线）→ 落位 → 双登记（外部注册表 + catalog）。

    dataset_type 默认 accuracy（题库）；perf 用于开源语料池场景（如
    ShareGPT：对话数组不经映射可直作语料池，配 fields 映射到扁平输入列
    则可作压测负载）。
    """
    check_type(dataset_type)
    from aite.datasets.add_hf import add_hf_dataset

    mapping = _parse_fields(fields)
    path = await run_in_threadpool(
        add_hf_dataset, hf_id, name, split=split, subset=subset, n=n,
        seed=seed, fields=mapping)
    ds_id = path.stem
    registry = userstore.load_registry().get(ds_id, {})
    return await register_entry(
        repo, dataset_type=dataset_type, name=ds_id, source="hf",
        path=str(path).replace("\\", "/"), version=split,
        stats={"count": registry.get("n")},
        notes=notes or f"hf:{hf_id}" + (f" ({subset})" if subset else ""),
        capabilities=capabilities, ownership=ownership)


async def remote_import(repo: Any, *, dataset_type: str, name: str,
                        url: str = "", notes: str = "",
                        force: bool = False,
                        capabilities: list[str] | None = None,
                        ownership: str | None = None) -> dict:
    """远程地址拉取（OBS 桶/任意 http 直链）→ 复用上传校验管线。

    url 缺省用设置里的 remote_url；结尾带 / 时按 ``<地址>/<name>.jsonl`` 拼接。
    """
    check_type(dataset_type)
    _check_name(name)
    target = (url or ds_settings.resolve_remote_url()).strip()
    if not target:
        raise ValueError(
            "未配置远程拉取地址：先在「路径与在线源配置」里填 remote_url"
            "（OBS 桶 https 地址或其他可直链下载数据集的地址）")
    if target.endswith("/"):
        target = target + f"{name}.jsonl"
    content = await run_in_threadpool(_http_get, target)
    return await upload_dataset(
        repo, dataset_type=dataset_type, name=name,
        filename=Path(target).name or f"{name}.jsonl", content=content,
        notes=notes or f"remote pull: {target}", force=force, source="remote",
        capabilities=capabilities, ownership=ownership)


def _probe(url: str, timeout: float = _PROBE_TIMEOUT_S) -> dict:
    """单地址连通性探测：收到 HTTP 响应即算通（401/403/404 也算网络可达）。"""
    started = time.perf_counter()
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        return {"ok": True, "status": resp.status_code,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": ""}
    except Exception as exc:  # 探测只报告不外抛（网络失败的形态五花八门）
        return {"ok": False, "status": None,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": f"{type(exc).__name__}: {exc}"[:200]}


def probe_online() -> dict:
    """HF 镜像与远程拉取地址的连通性（前端「和 HF 通/不通」决策依据）。"""
    endpoint = ds_settings.resolve_hf_endpoint()
    probe_url = endpoint.rstrip("/") + "/api/datasets?limit=1"
    remote_url = ds_settings.resolve_remote_url()
    remote = {"url": remote_url, "configured": bool(remote_url)}
    if remote_url:
        remote.update(_probe(remote_url))
    else:
        remote.update({"ok": False, "status": None, "latency_ms": 0,
                       "error": "未配置 remote_url（内网 HF 不通场景才需要）"})
    return {
        "hf": {"endpoint": endpoint, "probe_url": probe_url, **_probe(probe_url)},
        "remote": remote,
        "hint": "hf ok → 走 HF 镜像导入；hf 不通 → 配置 remote_url（OBS 桶等）走远程导入",
    }


def _count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def _origin(source: str, upstream: str = "") -> str:
    """来源类别（列表「本地/在线」维度）：在线 = HF 镜像或远程直链拉取的。"""
    if source in ("hf", "remote") or upstream.startswith("hf:"):
        return "online"
    return "local"


def merge_local_rows(rows: list[dict], only: str | None = None) -> list[dict]:
    """catalog 行 ⊕ 本机实际（外部注册表 + 数据集目录扫描），仅展示层合并。

    - catalog 行补 ``registered: true`` 与 ``origin``（local/online，回答
      「数据集是本地导入的还是在线拉的」）；
    - 注册表里有、catalog 没登记的（早期 add_hf 产物等已下载数据集）合成
      ``registered: false`` 行（source=registry）；
    - 数据集目录里两边都没登记的孤儿文件合成 ``registered: false`` 行
      （source=local-file，行数即规模）：accuracy 直文件、functional
      ``<名>/cases.jsonl``、perf 直文件（.stats.json 不算数据集）。
    - ``only`` 传类型时只扫/只合该类型（列表按类型过滤时计数才准确）。

    catalog 仍是写侧唯一权威（原则 6）；本函数只回答「本机现在有什么」。
    """
    registry = userstore.load_registry()
    root = userstore.data_root()
    known: set[tuple[str, str]] = set()
    seen_paths: set[str] = set()      # catalog 已引用的文件（perf 登记名≠文件名）
    out: list[dict] = []
    for r in rows:
        row = dict(r)
        row["registered"] = True
        row["origin"] = _origin(row.get("source", ""))
        # 私有性兜底：存量行 ownership 列为 NULL（迁移加列）→ 按 source 推导展示
        row["ownership"] = (row.get("ownership")
                            or ownership_from_source(row.get("source", "")))
        known.add((row["type"], row["name"]))
        if row.get("path"):
            seen_paths.add(str(row["path"]).replace("\\", "/"))
        if row["type"] == "accuracy":
            path = Path(row["path"]) if row.get("path") else None
            entry = registry.get(row["name"]) or {}
            cand = root / entry["file"] if entry.get("file") else None
            if (not path or not path.is_file()) and cand and cand.is_file():
                row["path"] = str(cand).replace("\\", "/")
        out.append(row)
    acc_known = {n for t, n in known if t == "accuracy"}
    if only in (None, "accuracy"):
        for name in sorted(k for k in registry if k not in acc_known):
            entry = registry[name]
            path = root / entry.get("file", f"datasets/accuracy/{name}.jsonl")
            if str(path).replace("\\", "/") in seen_paths:
                continue   # 同文件已按他型登记（如 ShareGPT 导为 perf），不再合成幻影行
            out.append({
                "id": f"local:{name}", "type": "accuracy", "name": name,
                "version": "", "source": "registry",
                "origin": _origin("registry", entry.get("upstream", "")),
                "ownership": ownership_from_source("registry",
                                                   entry.get("upstream", "")),
                "schema_version": "1",
                "stats": {"count": entry.get("n")},
                "capabilities": [],
                "path": str(path).replace("\\", "/") if path.is_file() else "",
                "notes": entry.get("task") or entry.get("upstream") or "",
                "registered": False,
            })
    orphans: list[tuple[str, Path, str]] = []   # (type, path, 展示名)
    acc_dir = userstore.dataset_dir("accuracy")
    if only in (None, "accuracy") and acc_dir.is_dir():
        orphans += [("accuracy", p, p.stem) for p in sorted(acc_dir.glob("*.jsonl"))]
    func_dir = userstore.dataset_dir("functional")
    if only in (None, "functional") and func_dir.is_dir():
        orphans += [("functional", p, p.parent.name)
                    for p in sorted(func_dir.glob("*/cases.jsonl"))]
    perf_dir = userstore.dataset_dir("perf")
    if only in (None, "perf") and perf_dir.is_dir():
        orphans += [("perf", p, p.stem) for p in sorted(perf_dir.glob("*.jsonl"))]
    for ds_type, p, name in orphans:
        if (ds_type, name) in known or name in registry \
                or str(p).replace("\\", "/") in seen_paths:
            continue
        out.append({
            "id": f"file:{p.stem}", "type": ds_type, "name": name,
            "version": "", "source": "local-file", "origin": "local",
            "ownership": "self",
            "schema_version": "1",
            "stats": {"count": _count_lines(p)},
            "capabilities": [],
            "path": str(p).replace("\\", "/"),
            "notes": "数据集目录中未登记的本地文件（导入一次即可入册）",
            "registered": False,
        })
    out.sort(key=lambda r: (r["type"], r["name"]))
    return out


def _norm_key(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _split_hf_id(text: str) -> str:
    """notes/upstream 里的 HF 仓 id：``hf:ceval/ceval-exam (val+dev..`` → ``ceval/ceval-exam``。"""
    m = re.search(r"hf:\s*([\w.-]+/[\w.-]+)", text or "")
    return m.group(1) if m else ""


def match_local_benchmarks(items: list[dict], benchmarks: list[dict]) -> list[dict]:
    """给基准全集打本机可用标记（纯函数）。

    - ``local``：强匹配 —— 基准 dataset_id 命中本地 notes/upstream 的 hf id，
      或基准 name 与本地数据集名归一（仅字母数字小写）后相等；
    - ``local_like``：近似 —— 两侧归一名互为前缀且长度 ≥4（如 ceval ↔ ceval_full）。
    """
    by_hf: dict[str, str] = {}
    by_norm: dict[str, str] = {}
    for r in items:
        if r.get("type") != "accuracy":
            continue
        by_norm.setdefault(_norm_key(str(r.get("name") or "")), r["name"])
        hid = _split_hf_id(str(r.get("notes") or ""))
        if hid:
            by_hf.setdefault(hid.lower(), r["name"])
    out = []
    for b in benchmarks:
        row = dict(b)
        hid = str(b.get("dataset_id") or "").lower()
        name_norm = _norm_key(str(b.get("name") or ""))
        row["local"] = by_hf.get(hid) or by_norm.get(name_norm)
        row["local_like"] = None
        if not row["local"] and len(name_norm) >= 4:
            for nrm, name in by_norm.items():
                if len(nrm) >= 4 and (nrm.startswith(name_norm)
                                      or name_norm.startswith(nrm)):
                    row["local_like"] = name
                    break
        out.append(row)
    return out


def _benchmarks_via_python_exe(adapter: Any) -> list[dict]:
    """主环境缺 evalscope 的回退：common.yaml 的 python_exe 子进程列举。"""
    from aite.core.profiles import load_common

    python_exe = (((load_common().get("tools") or {}).get("extra_args"))
                  or {}).get("python_exe")
    via = getattr(adapter, "list_tasks_via", None)
    if not via or not python_exe:
        raise RuntimeError(
            "主环境无法导入 evalscope，且 data/common.yaml 未配置 "
            "tools.extra_args.python_exe（独立环境解释器）——在线数据集不可列举")
    try:
        return via(str(python_exe))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"benchmarks 子进程列举超时（{python_exe}）") from exc


_ONLINE_CACHE: dict[str, list[dict]] = {}


def online_benchmarks(engine: str = "evalscope") -> dict:
    """引擎原生基准全集（BENCHMARK_REGISTRY 静态清单，进程内缓存一次列举）。"""
    cached = engine in _ONLINE_CACHE
    if not cached:
        from aite.tools import get_tool

        adapter = get_tool(engine)
        try:
            tasks = adapter.list_tasks()
        except ImportError:
            tasks = _benchmarks_via_python_exe(adapter)
        # 能力维度/私有性富化：evalscope 基准恒为开源（列举一次算好，缓存复用）
        _ONLINE_CACHE[engine] = [
            {**t, "capabilities": tags_to_capabilities(t.get("tags")),
             "ownership": "open"}
            for t in tasks]
    return {"engine": engine, "items": _ONLINE_CACHE[engine],
            "count": len(_ONLINE_CACHE[engine]), "cached": cached}


def reset_online_cache() -> None:
    """测试与长驻进程的缓存出口（BENCHMARK_REGISTRY 随环境变化才需要）。"""
    _ONLINE_CACHE.clear()


__all__ = ["hf_import", "match_local_benchmarks", "merge_local_rows",
           "online_benchmarks", "probe_online", "remote_import",
           "reset_online_cache", "resolve_download", "upload_dataset"]
