"""工作区钩子状态：每条声明是否已获 owner 信任、context_sync 是否在当前会话跑过。

ZCode 对项目钩子逐条做信任审批（~/.zcode/security/workspace-hook-trust-v1.json，键是
声明摘要）：新增或改动的声明在新会话启动时弹出审批，10 分钟内有效，批准前不运行，已批准
的声明不受影响。摘要算法未公开，这里按授权时记下的「事件 + 命令行 + matcher」比对；只改
超时、statusMessage 这类不进命令行的字段时摘要也会变，这里看不出来。

用法::

    python scripts/hooks/hook_status.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".zcode" / "config.json"
TRUST_STORE = Path.home() / ".zcode" / "security" / "workspace-hook-trust-v1.json"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import context_sync  # noqa: E402  快照目录与命名口径以钩子本身为准
import session_stats  # noqa: E402


def declarations() -> list[dict]:
    hooks = json.loads(CONFIG.read_text(encoding="utf-8")).get("hooks", {})
    found = []
    for event, groups in hooks.get("events", {}).items():
        for group in groups:
            for hook in group.get("hooks", []):
                found.append({"event": event, "matcher": group.get("matcher"),
                              "display": " ".join([hook.get("command", ""), *hook.get("args", [])])})
    return found


def trust_records() -> list[dict]:
    try:
        records = json.loads(TRUST_STORE.read_text(encoding="utf-8")).get("records", [])
    except (OSError, ValueError):
        return []
    return [r for r in records if Path(str(r.get("workspaceIdentity", ""))) == ROOT]


def grant_of(decl: dict, records: list[dict]) -> dict | None:
    for record in records:
        matcher = record.get("matcherAtGrant")
        if (record.get("decision") == "trusted" and record.get("eventAtGrant") == decl["event"]
                and record.get("displayCommandAtGrant") == decl["display"]
                and (matcher or None) == (decl["matcher"] or None)):
            return record
    return None


def current_session() -> str | None:
    try:
        return session_stats.resolve_session(session_stats.connect(), None, marker="hook_status")
    except (SystemExit, sqlite3.Error):
        return None


def main() -> int:
    records = trust_records()
    pending = 0
    print(f"工作区钩子（{CONFIG.relative_to(ROOT)}）：")
    for decl in declarations():
        grant = grant_of(decl, records)
        pending += grant is None
        state = f"已批准 {grant['grantedAt'][:16].replace('T', ' ')} UTC" if grant else "待批准"
        script = Path(decl["display"].split()[-1]).name
        matcher = f"  matcher={decl['matcher']}" if decl["matcher"] else ""
        print(f"  {decl['event']:17s} {state:26s} {script}{matcher}")
    snaps = sorted(context_sync.STATE_DIR.glob("agents-*.md"), key=lambda p: p.stat().st_mtime)
    session = current_session()
    mine = session and context_sync._snapshot_path(session).exists()
    last = (f"最近一次 {time.strftime('%m-%d %H:%M', time.localtime(snaps[-1].stat().st_mtime))}"
            if snaps else "从未运行")
    print(f"context_sync 运行痕迹：{last}；当前会话（{session or '未识别'}）："
          f"{'已运行' if mine else '未运行'}")
    if pending:
        print(f"{pending} 条待批准：新会话启动时 ZCode 会弹出工作区钩子审批（10 分钟内有效），批准后生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
