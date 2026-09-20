"""用例管理服务（§15，T 系列）：文字用例 CRUD 的枚举/绑定校验与编号生成。

校验原则（原则 9）：域/优先级/风险/状态枚举封闭；脚本绑定按域校验定位键
（缺键/未知键 fail-fast）；case_no 缺省自动生成 TC-<域代码>-<序号>。
"""

from __future__ import annotations

from typing import Any

_DOMAIN_PREFIX = {"functional": "FUNC", "accuracy": "ACC",
                  "perf": "PERF", "reliability": "REL"}
DOMAINS = tuple(_DOMAIN_PREFIX)
PRIORITIES = ("P0", "P1", "P2", "P3")
RISKS = ("高", "中", "低")
STATUSES = ("draft", "active", "deprecated")
_SCRIPT_KINDS = ("scenario_case", "suite_metric", "suite_case")

_DOC_FIELDS = ("case_no", "domain", "title", "purpose", "steps", "expected",
               "priority", "risk", "status", "script", "dataset_refs",
               "tags", "owner")


def _enum(value: str, allowed: tuple[str, ...], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"{label} 须为 {'/'.join(allowed)}，实际 {value!r}")
    return value


def validate_script(domain: str, script: dict) -> dict:
    """脚本绑定按域校验定位键（§15.2）。"""
    if not script:
        return {}
    kind = str(script.get("kind") or "")
    if kind not in _SCRIPT_KINDS:
        raise ValueError(
            f"script.kind 须为 {'/'.join(_SCRIPT_KINDS)}，实际 {kind!r}")
    if kind == "scenario_case":
        if domain != "functional":
            raise ValueError("script.kind=scenario_case 仅用于 functional 域")
        for key in ("scenario", "case_id"):
            if not script.get(key):
                raise ValueError(f"scenario_case 绑定缺 {key}")
    if kind == "suite_metric":
        if domain not in ("accuracy", "perf"):
            raise ValueError("script.kind=suite_metric 用于 accuracy/perf 域")
        if not script.get("metric"):
            raise ValueError("suite_metric 绑定缺 metric")
    if kind == "suite_case":
        if domain != "reliability":
            raise ValueError("script.kind=suite_case 仅用于 reliability 域")
        if not script.get("case_name"):
            raise ValueError("suite_case 绑定缺 case_name")
    return script


async def create_case_doc(repo: Any, fields: dict[str, Any]) -> dict:
    domain = _enum(str(fields.get("domain") or ""), DOMAINS, "domain")
    title = str(fields.get("title") or "").strip()
    if not title:
        raise ValueError("title 必填")
    priority = _enum(str(fields.get("priority") or "P2"), PRIORITIES, "priority")
    risk = _enum(str(fields.get("risk") or "中"), RISKS, "risk")
    status = _enum(str(fields.get("status") or "draft"), STATUSES, "status")
    script = validate_script(domain, dict(fields.get("script") or {}))
    case_no = str(fields.get("case_no") or "").strip()
    if case_no:
        if await repo.find_case_doc_by_no(case_no):
            raise ValueError(f"用例编号已存在: {case_no}")
    else:
        case_no = await _next_case_no(repo, domain)
    entry = {"case_no": case_no, "domain": domain, "title": title,
             "purpose": str(fields.get("purpose") or ""),
             "steps": str(fields.get("steps") or ""),
             "expected": str(fields.get("expected") or ""),
             "priority": priority, "risk": risk, "status": status,
             "script": script,
             "dataset_refs": list(fields.get("dataset_refs") or []),
             "tags": list(fields.get("tags") or []),
             "owner": str(fields.get("owner") or "")}
    return await repo.create_case_doc(entry)


async def update_case_doc(repo: Any, ref: str, fields: dict[str, Any]) -> dict:
    row = await _resolve(repo, ref)
    updates: dict[str, Any] = {}
    if "domain" in fields:
        raise ValueError("domain 建后不可改（新建用例替代）")
    if "title" in fields:
        title = str(fields["title"] or "").strip()
        if not title:
            raise ValueError("title 不可为空")
        updates["title"] = title
    for key in ("purpose", "steps", "expected", "owner"):
        if key in fields:
            updates[key] = str(fields[key] or "")
    for key, allowed in (("priority", PRIORITIES), ("risk", RISKS),
                         ("status", STATUSES)):
        if key in fields:
            updates[key] = _enum(str(fields[key]), allowed, key)
    if "script" in fields:
        updates["script"] = validate_script(row["domain"],
                                            dict(fields["script"] or {}))
    if "dataset_refs" in fields:
        updates["dataset_refs"] = list(fields["dataset_refs"] or [])
    if "tags" in fields:
        updates["tags"] = list(fields["tags"] or [])
    updated = await repo.update_case_doc(row["id"], **updates)
    if updated is None:
        raise ValueError(f"用例不存在: {ref}")
    return updated


async def _resolve(repo: Any, ref: str) -> dict:
    row = await repo.get_case_doc(ref) or await repo.find_case_doc_by_no(ref)
    if row is None:
        raise ValueError(f"未知用例: {ref!r}（编号或 id）")
    return row


async def resolve_case_ref(repo: Any, ref: str) -> dict:
    return await _resolve(repo, ref)


async def _next_case_no(repo: Any, domain: str) -> str:
    prefix = f"TC-{_DOMAIN_PREFIX[domain]}-"
    existing = [r["case_no"] for r in
                await repo.list_case_docs(domain=domain, limit=10_000)]
    seq = 1
    taken = set(existing)
    while f"{prefix}{seq:04d}" in taken:
        seq += 1
    return f"{prefix}{seq:04d}"
