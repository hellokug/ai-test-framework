"""注入对象选择引擎（§14.1 两级漏斗注入层）：match/exclude/select 活体解析。

- match（必填单键）：``contains`` / ``regex`` 匹配进程名+命令行（python 类进程
  名无信息量，靠命令行定位是常态）；``exact`` / ``prefix`` 仅匹配进程名。
- exclude：match 键清单 + ``{system: true}``（系统账号进程一律排除）；
  自身 pid 恒排除。
- select：``first``（pid 最小，确定性）/ ``all`` / ``random(k)``（seed 可复现）/
  ``percent(p)``（至少 1 个，随机）/ ``count(n)``（pid 升序前 n，确定性）。
- 解析结果为空显式 ValueError（原则 9）——静默空注入等于没做实验。
"""

from __future__ import annotations

import os
import random
import re
from typing import Any

_MATCH_KEYS = ("contains", "exact", "prefix", "regex")
_SYSTEM_TOKENS = ("system", "network service", "local service", "root")


def _psutil():
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("注入对象解析需要 psutil：pip install psutil") from exc
    return psutil


def parse_process_spec(process_spec: dict[str, Any]) -> tuple[dict, list[dict], dict]:
    """inject.process → (match 单键 dict, exclude 清单, select dict)。"""
    if not isinstance(process_spec, dict) or not process_spec:
        raise ValueError("inject.process 须为对象（匹配键 + exclude/select 可选）")
    exclude = list(process_spec.get("exclude") or [])
    select = dict(process_spec.get("select") or {})
    match = {k: v for k, v in process_spec.items() if k not in ("exclude", "select")}
    unknown = [k for k in match if k not in _MATCH_KEYS]
    if len(match) != 1 or unknown:
        raise ValueError(f"inject.process 须恰好一个匹配键 {list(_MATCH_KEYS)}，"
                         f"实际: {sorted(match) or '无'}")
    return match, exclude, select


def _match_one(match: dict, name: str, cmdline: str) -> bool:
    key = next(iter(match))
    value = str(match[key])
    if key == "contains":
        v = value.lower()
        return v in name.lower() or v in cmdline.lower()
    if key == "exact":
        return name == value
    if key == "prefix":
        return name.startswith(value)
    return re.search(value, cmdline) is not None or re.search(value, name) is not None


def _is_system(username: str) -> bool:
    u = (username or "").split("\\")[-1].lower()
    return any(t in u for t in _SYSTEM_TOKENS)


def _apply_select(pids: list[int], select: dict, seed: int) -> list[int]:
    pids = sorted(pids)
    mode = str(select.get("mode") or "all")
    value = select.get("value")
    rng = random.Random(seed)
    if mode == "first":
        return pids[:1]
    if mode == "all":
        return pids
    if mode == "random":
        k = max(1, int(value or 1))
        return sorted(rng.sample(pids, min(k, len(pids))))
    if mode == "percent":
        p = float(value or 0)
        if not 0 < p <= 100:
            raise ValueError(f"select.percent.value 须在 (0, 100]: {p}")
        k = max(1, round(len(pids) * p / 100))
        return sorted(rng.sample(pids, min(k, len(pids))))
    if mode == "count":
        return pids[:max(1, int(value or 1))]
    raise ValueError(f"未知 select.mode: {mode!r}"
                     "（可用: first/all/random/percent/count）")


def resolve_processes(match: dict, exclude: list[dict] | None = None,
                      select: dict | None = None, *, seed: int = 0) -> list[int]:
    """活体解析注入单元：match 命中 → exclude 排除 → select 定数量（pid 升序）。"""
    if not isinstance(match, dict) or not match:
        raise ValueError(f"inject.process 缺匹配键（{_MATCH_KEYS}）")
    psutil = _psutil()
    excludes = list(exclude or [])
    ex_match = [e for e in excludes if isinstance(e, dict)
                and any(k in _MATCH_KEYS for k in e)]
    ex_system = any(isinstance(e, dict) and e.get("system") for e in excludes)
    self_pid = os.getpid()

    picked: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "username"]):
        try:
            info = proc.info
        except Exception:  # noqa: BLE001 - 进程瞬间退出属正常噪音
            continue
        pid = info.get("pid")
        name = info.get("name") or ""
        cmdline = " ".join(info.get("cmdline") or [])
        if pid == self_pid or not _match_one(match, name, cmdline):
            continue
        if ex_system and _is_system(info.get("username") or ""):
            continue
        if any(_match_one(e, name, cmdline) for e in ex_match):
            continue
        picked.append(pid)
    if not picked:
        raise ValueError(f"注入对象解析为空：match={match}，"
                         f"exclude={ex_match or '无'}"
                         "（请用发现层/树视图核对关键字）")
    return _apply_select(picked, select or {}, seed)
