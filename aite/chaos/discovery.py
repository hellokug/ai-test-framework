"""发现层（模块 I6 拓扑感知一期）：故障注入对象的 查询 → 节点>容器>进程 视图。

匹配规则（全部大小写不敏感的子串模糊匹配）：
- node / node_ip：对主机名与本机 IP 列表；
- container / container_ip：对容器名/ID/镜像与容器 IP；
- process：对进程名与完整命令行。

边界（原则 12）：本机节点经 psutil（懒加载降级）；容器层经 docker CLI 子进程
（JSON 输出契约，CLI 缺失/超时降级为 note，不抛错）；跨节点发现依赖执行机
agent（模块 G 二期），当前仅本机。容器内进程经 ``docker top`` 尽力而为。
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess

from aite.utils.logging import get_logger

logger = get_logger(__name__)

_DOCKER_TIMEOUT = 5          # docker ps/inspect/top 单命令超时（秒）
_PROCESS_SKIP = {"system", "idle", "registry"}   # psutil 无权限进程名的噪音过滤


def _fuzzy(needle: str | None, *haystacks: str | None) -> bool:
    """大小写不敏感子串匹配；needle 为空 = 不筛。"""
    if not needle:
        return True
    n = str(needle).strip().lower()
    return any(n in str(h).lower() for h in haystacks if h)


def _local_ips() -> list[str]:
    """本机 IP 列表（UDP connect 探测默认路由 + hostname 解析，尽力而为）。"""
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if "." in ip:
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def _docker(args: list[str]) -> list[dict] | None:
    """docker CLI 子进程 → JSON 行/对象；不可用返回 None（调用方降级）。"""
    try:
        proc = subprocess.run(["docker", *args], capture_output=True,
                              text=True, timeout=_DOCKER_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    if not out:
        return []
    try:
        return json.loads(out) if out.startswith("[") or out.startswith("{") else \
            [json.loads(line) for line in out.splitlines() if line.strip()]
    except ValueError:
        return None


def _containers() -> tuple[list[dict], list[str]]:
    """容器列表（docker ps + inspect 补 IP）与降级说明。"""
    rows = _docker(["ps", "--format", "{{json .}}"])
    if rows is None:
        return [], ["docker CLI 不可用或守护进程未运行——容器层发现跳过"]
    names = [str(r.get("Names") or r.get("name") or "").lstrip("/")
             for r in rows]
    ips: dict[str, str] = {}
    if names:
        inspect = _docker(["inspect", "--format",
                           "{{.Name}} {{.NetworkSettings.IPAddress}}", *names])
        if inspect:
            for item in inspect:
                parts = str(item).split()
                if len(parts) == 2:
                    ips[parts[0].lstrip("/")] = parts[1]
    containers = []
    for r in rows:
        name = str(r.get("Names") or r.get("name") or "").lstrip("/")
        containers.append({"name": name,
                           "id": str(r.get("ID") or r.get("id") or ""),
                           "image": str(r.get("Image") or r.get("image") or ""),
                           "state": str(r.get("State") or r.get("state") or ""),
                           "ip": ips.get(name, ""),
                           "processes": _docker_top(name)})
    return containers, []


def _docker_top(name: str) -> list[dict]:
    """容器内进程（docker top，尽力而为：失败返回空）。"""
    rows = _docker(["top", name, "-o", "pid,comm"])
    if not rows:
        return []
    out = []
    for r in rows:
        try:
            out.append({"pid": int(r.get("PID") or r.get("pid") or 0),
                        "name": str(r.get("CMD") or r.get("comm") or "")})
        except (TypeError, ValueError):
            continue
    return out


def _processes() -> tuple[list[dict], list[str]]:
    """本机进程列表（psutil 懒加载；缺失降级为 note）。"""
    try:
        import psutil
    except ImportError:
        return [], ["psutil 未安装——进程层发现跳过（pip install psutil）"]
    out = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "username"]):
        try:
            info = proc.info
        except Exception:  # noqa: BLE001 - 进程瞬间退出属正常噪音
            continue
        name = info.get("name") or ""
        if name.lower() in _PROCESS_SKIP:
            continue
        out.append({"pid": info.get("pid"), "name": name,
                    "cmdline": " ".join(info.get("cmdline") or []),
                    "username": info.get("username") or ""})
    return out, []


def discover_targets(filters: dict | None = None) -> dict:
    """故障注入对象发现：模糊过滤 → 节点>容器>进程 树视图。

    filters 键（全部可选、大小写不敏感子串）：node / node_ip / container /
    container_ip / process。空 filters 返回全量树。
    """
    f = {k: (str(v).strip() if v else "") for k, v in (filters or {}).items()}
    notes: list[str] = []
    hostname = socket.gethostname()
    ips = _local_ips()

    node_hit = (_fuzzy(f.get("node"), hostname)
                and _fuzzy(f.get("node_ip"), *ips))

    containers: list[dict] = []
    if node_hit and (f.get("container") or f.get("container_ip") or True):
        rows, container_notes = _containers()
        notes.extend(container_notes)
        for c in rows:
            if (_fuzzy(f.get("container"), c["name"], c["id"], c["image"])
                    and _fuzzy(f.get("container_ip"), c["ip"])):
                if f.get("process"):
                    c["processes"] = [p for p in c["processes"]
                                      if _fuzzy(f.get("process"), p["name"])]
                containers.append(c)

    processes: list[dict] = []
    if node_hit:
        rows, proc_notes = _processes()
        notes.extend(proc_notes)
        processes = [p for p in rows if _fuzzy(f.get("process"),
                                               p["name"], p["cmdline"])]

    return {
        "node": {"name": hostname, "ips": ips, "os": platform.system(),
                 "pid": os.getpid()},
        "matched": node_hit or bool(containers),
        "containers": containers,
        "processes": processes,
        "counts": {"containers": len(containers), "processes": len(processes)},
        "notes": notes,
    }
