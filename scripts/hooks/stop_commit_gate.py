"""Stop hook 门禁：工作树积压未提交改动时阻止收尾（owner 2026-11-12 确认）。

背景：多会话并行 + 「按域小步提交」只靠自觉无效——2026-11-08→11-12 积压 128 个
文件。由 ``.zcode/config.json`` 注册到 ZCode 的 Stop 事件（process 类型）。判定：

- ``git status --porcelain`` 干净 → exit 0。
- 最终回复含 DEFER_MARKER（``<!--commit-deferred: 原因-->``）→ exit 0（显式延后留痕）。
- 非噪音未提交改动里最老 mtime 距今 ≥ STALE_MINUTES 分钟 → exit 2 阻止结束：
  stderr 给积压规模（按顶层目录分组）、最老文件与年龄，要求按域提交测试通过的
  完整单元；并行会话认领中的文件不代提交（认领表见 docs/session-claims.md）。
- 噪音排除：session-claims（活租约表）/ efficiency-log（账本）按设计常驻改动。
- fail-open：git 缺失/超时、stdin 不可读 → exit 0，不挡收尾。

手工验证::

    echo '{}' | python scripts/hooks/stop_commit_gate.py   # 树脏且够老 → exit 2
    AITE_HOOK_PROJECT_DIR=<干净目录> echo '{}' | python ...  # exit 0
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(os.environ.get("AITE_HOOK_PROJECT_DIR")
                   or Path(__file__).resolve().parents[2])
CLAIMS = PROJECT_DIR / "docs" / "session-claims.md"
DEFER_MARKER = "<!--commit-deferred:"
STALE_MINUTES = 45
GIT_TIMEOUT = 10
# 协作活文档按设计常驻改动，不计积压
NOISE = {"docs/session-claims.md", "docs/efficiency-log.md"}


def _release() -> None:
    sys.exit(0)


def _block(reason: str) -> None:
    print(reason, file=sys.stderr)
    sys.exit(2)


def _stdin() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _expand_braces(token: str) -> list[str]:
    """一层花括号展开：aite/storage/{models,repositories}.py → 两个具体路径。"""
    match = re.search(r"\{([^{}]+)\}", token)
    if not match:
        return [token]
    head, tail = token[:match.start()], token[match.end():]
    return [f"{head}{option}{tail}" for option in match.group(1).split(",")]


def _scope_tokens(scope: str) -> list[str]:
    """按 +/、/逗号/空白切分认领范围；花括号内的逗号不切（留给花括号展开）。"""
    tokens: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in scope:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if depth == 0 and char in "+、,， \t":
            if buf:
                tokens.append("".join(buf))
                buf = []
        else:
            buf.append(char)
    if buf:
        tokens.append("".join(buf))
    return tokens


def _claimed_prefixes() -> list[str]:
    """「## 在认领」段各行的路径前缀（花括号展开后）；表缺失/无路径 → 空表。

    mtime 老 ≠ 写它的会话已收尾：在途会话靠认领表声明「正在写什么」，
    命中认领范围的文件一律豁免，避免长任务会话被自己的早期文件卡住收尾。
    """
    try:
        text = CLAIMS.read_text(encoding="utf-8")
    except OSError:
        return []
    section = text.split("## 在认领", 1)[-1].split("## ", 1)[0]
    prefixes: list[str] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        parts = line[2:].split("｜")
        if len(parts) < 3:
            continue
        for token in _scope_tokens(parts[2]):
            token = token.strip().replace("\\", "/").rstrip("*")
            if "/" in token or "." in token:
                prefixes.extend(_expand_braces(token))
    return prefixes


def _is_claimed(path: str, prefixes: list[str]) -> bool:
    posix = path.replace("\\", "/")
    return any(posix.startswith(prefix) for prefix in prefixes)


def _dirty_entries() -> list[tuple[str, str]]:
    """porcelain v1 → [(状态, 路径)]；git 不可用时抛错由调用方兜底放行。"""
    done = subprocess.run(
        ["git", "-C", str(PROJECT_DIR), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, timeout=GIT_TIMEOUT)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or "git status failed")
    entries = []
    for line in done.stdout.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2].strip() or "M", line[3:].strip()
        if " -> " in path:                       # 重命名记新路径（旧路径已无 mtime）
            path = path.split(" -> ", 1)[1]
        entries.append((status, path.strip('"')))
    return entries


def _oldest_age_minutes(entries: list[tuple[str, str]]) -> tuple[float, str] | None:
    """最老未提交改动的 (距今分钟数, 路径)；删除项/取不到 mtime 的跳过。"""
    now = time.time()
    oldest: tuple[float, str] | None = None
    for status, path in entries:
        if status.startswith("D"):
            continue
        try:
            mtime = (PROJECT_DIR / path).stat().st_mtime
        except OSError:
            continue
        if oldest is None or mtime < oldest[0]:
            oldest = (mtime, path)
    if oldest is None:
        return None
    return (now - oldest[0]) / 60, oldest[1]


def _grouped(entries: list[tuple[str, str]]) -> str:
    """按顶层目录归组的积压概览（最多 8 组）。"""
    counts: dict[str, int] = {}
    for _status, path in entries:
        parts = Path(path).parts
        key = "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if parts else path)
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
    return "、".join(f"{key}×{count}" for key, count in top)


def main() -> None:
    data = _stdin()
    if data.get("stop_hook_active") or data.get("stopHookActive"):
        _release()
    text = str(data.get("responseText") or data.get("last_assistant_message")
               or data.get("responsePreview") or "")
    if DEFER_MARKER in text:
        _release()
    try:
        claimed = _claimed_prefixes()
        entries = [entry for entry in _dirty_entries()
                   if entry[1] not in NOISE and not _is_claimed(entry[1], claimed)]
    except Exception:
        _release()
    if not entries:
        _release()
    stale = _oldest_age_minutes(entries)
    if stale is None or stale[0] < STALE_MINUTES:
        _release()
    age, oldest_path = stale
    in_flight = f"（另有 {len(claimed)} 条认领路径下的在途文件已豁免）" if claimed else ""
    _block(f"提交门禁：工作树有 {len(entries)} 个不在任何活认领范围、且最老改动已超 "
           f"{STALE_MINUTES} 分钟的未提交文件（{_grouped(entries)}；最老的 {oldest_path} "
           f"躺了 {age / 60:.1f} 小时）{in_flight}。把测试通过的完整单元按域提交"
           "（git add <该域文件> && git commit；新文件别忘 untracked）；确认现在不宜提交"
           f"则在最终回复加 {DEFER_MARKER} 原因--> 显式延后。若是并行会话在写而没登记，"
           "让它先在 docs/session-claims.md 登记认领（登记即豁免）；不要代提交别的会话"
           "认领中的文件。")


if __name__ == "__main__":
    main()
