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
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(os.environ.get("AITE_HOOK_PROJECT_DIR")
                   or Path(__file__).resolve().parents[2])
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
        entries = [entry for entry in _dirty_entries() if entry[1] not in NOISE]
    except Exception:
        _release()
    if not entries:
        _release()
    stale = _oldest_age_minutes(entries)
    if stale is None or stale[0] < STALE_MINUTES:
        _release()
    age, oldest_path = stale
    _block(f"提交门禁：工作树积压 {len(entries)} 个未提交改动（{_grouped(entries)}），"
           f"最老的 {oldest_path} 已躺 {age / 60:.1f} 小时。把测试通过的完整单元按域提交"
           "（git add <该域文件> && git commit；新文件别忘 untracked）；确认现在不宜提交"
           f"（如其余是并行会话在途文件）则在最终回复加 {DEFER_MARKER} 原因--> 显式延后。"
           "不要代提交别的会话认领中的文件（docs/session-claims.md）。")


if __name__ == "__main__":
    main()
