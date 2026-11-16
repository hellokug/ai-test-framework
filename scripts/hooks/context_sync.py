"""会话上下文同步钩子：会话中途的规则变更、效率账本未结项与多会话认领主动送达模型。

AGENTS.md 只在会话启动时注入系统提示，并行会话或 owner 中途改的规则本会话看不到
（2026-11-09 复盘：IAB 规则已入库，本会话照旧踩坑约 8 次调用）。由
``.zcode/config.json`` 注册到三个事件（process 类型，``-I -S`` 冷启动约 0.15s）：

- ``SessionStart``：记 AGENTS.md 基线快照；注入 docs/efficiency-log.md 未结项与
  docs/session-claims.md 在认领（AGENTS.md「闭环」「多会话并行认领」要求开工前
  过一遍，改为主动推送）。resume/compact 保留基线并报差异。
- ``UserPromptSubmit`` / ``PostToolUse``（matcher 只挂 Bash 与浏览器工具）：AGENTS.md
  与快照不一致 → 注入差异并刷新快照；docs/session-claims.md 有变化 → 注入当前
  在认领并刷新快照（各处变更只推一次，并行会话的认领/释放即由此送达）。
- ``UserPromptSubmit`` 另查 ZCode 计量库：上一次主流程请求的上下文超过 15 万 token 时提醒
  新任务开新会话（实测 17 万以上单次请求中位 34 秒，6 万以下约 4 秒）。

输出 ``{"hookSpecificOutput": {"hookEventName", "additionalContext"}}``（ZCode 严格
schema，多一个键即校验失败）；任何异常静默 exit 0（fail-open，不打断会话）。
stdin 是 UTF-8 JSON：``hook_event_name`` / ``session_id`` / ``cwd`` / ``transcript_path``，
另按事件带 ``source``（startup|resume|clear|compact）、``prompt``、``tool_name`` +
``tool_input``（工具事件）。新增或改动的钩子声明要 owner 在 ZCode 工作区钩子信任审批里
批准才运行（逐条记在 ``~/.zcode/security/workspace-hook-trust-v1.json``）。
快照存系统临时目录 aite-zcode-hooks/（agents-*.md / claims-*.md），超过 7 天的
在 SessionStart 时清理。

手工验证::

    echo '{"hook_event_name":"SessionStart","session_id":"t1","source":"startup"}' \\
      | .venv/Scripts/python.exe -I -S scripts/hooks/context_sync.py
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
EFF_LOG = ROOT / "docs" / "efficiency-log.md"
CLAIMS = ROOT / "docs" / "session-claims.md"
STATE_DIR = Path(os.environ.get("TEMP") or os.environ.get("TMP") or Path.home()) / "aite-zcode-hooks"
USAGE_DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"

MAX_DIFF_LINES = 60
MAX_OPEN_ITEMS = 10      # 对齐 AGENTS.md「尾部 10 条」
MAX_CLAIM_LINES = 10     # 租约表正常 0～3 行，异常多时也只注入前 10 条
MAX_CONTEXT_CHARS = 6000
SNAPSHOT_TTL_S = 7 * 86400
CONTEXT_WARN_TOKENS = 150_000   # 对齐 AGENTS.md「提速规则 · 上下文」
DONE_STATUSES = {"done", "closed", "已完成"}
CLAIMS_SECTION = "## 在认领"
CLAIMS_EMPTY = "（当前无）"
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


def _payload() -> dict:
    """stdin 按 UTF-8 解码：不依赖宿主是否注入 PYTHONUTF8（-I 模式下也会忽略它）。"""
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _safe_name(session_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)[:120]


def _snapshot_path(session_id: str, kind: str = "agents") -> Path:
    return STATE_DIR / f"{kind}-{_safe_name(session_id)}.md"


def _write_snapshot(session_id: str, text: str, kind: str = "agents") -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _snapshot_path(session_id, kind).write_text(text, encoding="utf-8")


def _render_delta(old: str, new: str) -> str:
    import difflib  # 仅在有变更时加载，无变更路径保持最短冷启动

    body = [line for line in difflib.unified_diff(old.splitlines(), new.splitlines(),
                                                  lineterm="", n=0)
            if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    shown = body[:MAX_DIFF_LINES]
    if len(body) > MAX_DIFF_LINES:
        shown.append(f"…（其余 {len(body) - MAX_DIFF_LINES} 行省略，Read AGENTS.md 看全文）")
    return ("AGENTS.md 在本会话加载后被修改（来自并行会话、owner 或你本会话的编辑）。"
            "系统提示里的是旧版，以下差异以本条为准（+ 新增 / - 删除）：\n" + "\n".join(shown))


def _agents_delta(session_id: str) -> str:
    """与快照比对：无快照 → 静默建基线；有差异 → 返回差异并刷新快照。"""
    current = _read(AGENTS)
    if current is None:
        return ""
    previous = _read(_snapshot_path(session_id))
    if previous == current:
        return ""
    _write_snapshot(session_id, current)
    return "" if previous is None else _render_delta(previous, current)


def _open_items() -> str:
    """docs/efficiency-log.md 表格里状态非 done 的行（尾部 MAX_OPEN_ITEMS 条）。"""
    rows = []
    for line in (_read(EFF_LOG) or "").splitlines():
        cells = [c.strip().replace("\\|", "|")
                 for c in _CELL_SPLIT.split(line.strip().strip("|"))]
        if len(cells) < 4 or not cells[0][:4].isdigit():
            continue  # 表头、分隔行、正文
        if any(cells[-1].lower().startswith(s) for s in DONE_STATUSES):
            continue  # 兼容「done（2026-11-16 核实说明）」带后缀的关账写法
        rows.append(f"- {cells[0]}｜{cells[1]}｜→ {cells[2]}")
    if not rows:
        return ""
    return ("docs/efficiency-log.md 未结项（AGENTS.md 闭环：开工前过一遍，"
            "能顺手做掉的做掉并把状态改 done）：\n" + "\n".join(rows[-MAX_OPEN_ITEMS:]))


def _active_claims() -> str:
    """docs/session-claims.md「## 在认领」下的条目（租约表，通常 0～3 行）。"""
    rows = []
    in_section = False
    for line in (_read(CLAIMS) or "").splitlines():
        if line.strip().startswith("## "):
            in_section = line.strip() == CLAIMS_SECTION
            continue
        if in_section and line.strip() and line.strip() != CLAIMS_EMPTY:
            rows.append(line.strip())
    if not rows:
        return ""
    return ("docs/session-claims.md 在认领（AGENTS.md「多会话并行认领」：动工先登记，"
            "收尾删行；撞域换域或排队）：\n" + "\n".join(rows[:MAX_CLAIM_LINES]))


def _claims_delta(session_id: str) -> str:
    """与快照比对：有变化 → 注入当前在认领并刷新快照（租约表小，不值得渲染 diff）。"""
    current = _read(CLAIMS)
    if current is None:
        return ""
    previous = _read(_snapshot_path(session_id, "claims"))
    if previous == current:
        return ""
    _write_snapshot(session_id, current, "claims")
    return "" if previous is None else _active_claims()


def _prune_snapshots() -> None:
    cutoff = time.time() - SNAPSHOT_TTL_S
    for path in STATE_DIR.glob("*.md"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _on_session_start(data: dict, session_id: str) -> str:
    _prune_snapshots()
    parts = []
    if data.get("source") in ("resume", "compact"):
        parts.append(_agents_delta(session_id))   # 系统提示未必重载：保留基线报差异
    else:
        current = _read(AGENTS)
        if current is not None:
            _write_snapshot(session_id, current)  # startup/clear：刚注入的就是当前版
    current_claims = _read(CLAIMS)
    if current_claims is not None:
        _write_snapshot(session_id, current_claims, "claims")
    parts.append(_open_items())
    parts.append(_active_claims())
    return "\n\n".join(p for p in parts if p)


def _on_check(data: dict, session_id: str) -> str:
    return "\n\n".join(p for p in (_agents_delta(session_id), _claims_delta(session_id)) if p)


def _context_warning(session_id: str) -> str:
    """上一次主流程请求的上下文过大 → 提醒新任务开新会话；查不到就不提醒。"""
    import sqlite3  # 只在提问时查库，工具事件路径不加载

    try:
        con = sqlite3.connect(f"file:{USAGE_DB}?mode=ro", uri=True, timeout=2)
        row = con.execute("select input_tokens from model_usage where session_id = ?"
                          " and query_source = 'main_turn' order by started_at desc limit 1",
                          (session_id,)).fetchone()
    except sqlite3.Error:
        return ""
    tokens = (row[0] or 0) if row else 0
    if tokens < CONTEXT_WARN_TOKENS:
        return ""
    return (f"本会话上一次请求的上下文已 {tokens / 1000:.0f}K token（实测 17 万以上单次请求中位"
            " 34 秒，6 万以下约 4 秒）。若本次提问与前文无关，在回复开头提醒 owner 开新会话会快得多，"
            "然后照常完成任务。")


def _on_prompt(data: dict, session_id: str) -> str:
    return "\n\n".join(p for p in (_agents_delta(session_id), _claims_delta(session_id),
                                   _context_warning(session_id)) if p)


HANDLERS = {
    "SessionStart": _on_session_start,
    "UserPromptSubmit": _on_prompt,
    "PostToolUse": _on_check,
}


def main() -> None:
    data = _payload()
    event = data.get("hook_event_name") or data.get("hookEventName")
    session_id = data.get("session_id") or data.get("sessionId")
    if event not in HANDLERS or not session_id:
        return
    text = HANDLERS[event](data, str(session_id))
    if text:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": text[:MAX_CONTEXT_CHARS],
        }}))


if __name__ == "__main__":
    with contextlib.suppress(Exception):  # fail-open：钩子故障不打断会话
        main()
    sys.exit(0)
