"""Stop hook 门禁：任务型回合结束前强制「量化」效率复盘。

由 ``.zcode/config.json`` 注册到 ZCode 的 Stop 事件（process 类型，无 shell，
Windows 安全）。判定逻辑：

- 证据源：stdin 的 ``toolCallCount`` / ``responseText`` / ``turnId``（ZCode Stop 载荷
  自带，最精确）；缺失时退回 ``transcript_path``（通用 JSONL，正则兜底）或用
  ``session_id`` 回查 ``~/.zcode/cli/rollout/model-io-<session_id>.jsonl``。
- 本轮工具调用 >= MIN_TOOL_CALLS 时：最终回复不含 MARKER，或复盘小节里没有「耗时 + 数字」
  与「token」→ exit 2 阻止结束。stderr 附本轮实测数字（scripts/session_stats.py 现算），
  模型补写时直接引用，不必再跑工具（框架侧最多连续补写 3 次，无死循环）。
- 其余一律 exit 0 放行：读不到输入/记录（fail-open）、琐碎问答、已量化、
  防循环标记（stop_hook_active）已置位。

手工验证::

    echo '{}' | python scripts/hooks/stop_efficiency_gate.py   # 退出码 0
    echo "{\"session_id\": \"sess_xxx\"}" | python ...          # 视该会话而定
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MARKER = "<!--eff-review-->"
MIN_TOOL_CALLS = 3
TAIL_CHARS = 4000  # transcript 兜底路径只看文件尾部
_QUANTIFIED = (re.compile(r"耗时[^\n]{0,8}?\d"), re.compile(r"token", re.I))

ROLLOUT_DIR = Path.home() / ".zcode" / "cli" / "rollout"


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
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _first(data: dict, *keys: str):
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def _rollout_path(session_id: str) -> Path | None:
    candidate = ROLLOUT_DIR / f"model-io-{session_id}.jsonl"
    if candidate.exists():
        return candidate
    matches = sorted(ROLLOUT_DIR.glob(f"model-io-*{session_id}*.jsonl"))
    return matches[-1] if matches else None


def _tools_since_last_user(messages: list, total: int) -> int:
    """最近一条 user 消息之后的 tool 消息数；找不到边界时退回全会话计数。"""
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], dict) and messages[index].get("role") == "user":
            return sum(
                1
                for m in messages[index + 1:]
                if isinstance(m, dict) and m.get("role") == "tool"
            )
    return total


def _from_rollout(path: Path) -> tuple[str, int] | None:
    """该会话最后一条 model-io 记录 -> (最后的助手文本, 本轮工具调用数)。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if not isinstance(entry, dict) or "response" not in entry:
            continue
        text = str((entry.get("response") or {}).get("text") or "")
        messages = (entry.get("request") or {}).get("messages") or []
        total = sum(
            1 for m in messages if isinstance(m, dict) and m.get("role") == "tool"
        )
        return text, _tools_since_last_user(messages, total)
    return None


def _from_transcript(path: Path) -> tuple[str, int] | None:
    """未知结构的通用 JSONL：正则保守计数，只影响是否多提醒一次。"""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    tool_calls = len(re.findall(r'"type"\s*:\s*"(?:tool_use|tool_call)"', raw))
    return raw[-TAIL_CHARS:], tool_calls


def _quantified(text: str) -> bool:
    """复盘小节（最后一个「效率复盘」之后）同时含「耗时 + 数字」与「token」。"""
    start = text.rfind("效率复盘")
    section = text[start:] if start >= 0 else text
    return all(pattern.search(section) for pattern in _QUANTIFIED)


def _stats_line(session_id: str | None, turn_id: str | None) -> str:
    """本轮实测「消耗」行（scripts/session_stats.py 现算）；取不到返回空串。"""
    if not session_id:
        return ""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import session_stats

        turns = session_stats.load_turns(session_stats.connect(), str(session_id))
        turn = next((t for t in turns if t.turn_id == turn_id), turns[-1] if turns else None)
        return session_stats.brief(turn) if turn else ""
    except Exception:
        return ""


def _evidence(data: dict) -> tuple[str, int] | None:
    """(最终回复文本, 本轮工具调用数)：优先 Stop 载荷自带字段，其次 transcript / rollout。"""
    text = _first(data, "responseText", "last_assistant_message", "responsePreview")
    count = data.get("toolCallCount")
    if isinstance(text, str) and isinstance(count, int):
        return text, count
    found = None
    transcript = _first(data, "transcript_path", "transcriptPath")
    if transcript:
        found = _from_transcript(Path(transcript))
    if found is None:
        session_id = _first(data, "session_id", "sessionId")
        rollout = _rollout_path(str(session_id)) if session_id else None
        if rollout:
            found = _from_rollout(rollout)
    if found is not None and isinstance(text, str):
        return text, found[1]
    return found


def main() -> None:
    data = _stdin()
    if data.get("stop_hook_active") or data.get("stopHookActive"):
        _release()
    found = _evidence(data)
    if found is None:
        _release()
    last_text, tool_calls = found
    if tool_calls < MIN_TOOL_CALLS:
        _release()
    has_marker = MARKER in last_text
    if has_marker and _quantified(last_text):
        _release()
    line = _stats_line(_first(data, "session_id", "sessionId"), _first(data, "turnId", "turn_id"))
    measured = (f"本轮实测：{line}。" if line
                else "实测数字用 python scripts/session_stats.py --brief 取。")
    if not has_marker:
        _block(f"效率复盘缺失：本轮是任务型工作（工具调用 {tool_calls} 次）。请在最终回复末尾补"
               f"『效率复盘』小节（结果/消耗/低效点/优化动作）并以 {MARKER} 收尾。{measured}"
               "「消耗」照抄这组数字；低效点按耗时最大的环节写并注明估计浪费的时间；规则类"
               "低效点当场固化进 AGENTS.md，其余记入 docs/efficiency-log.md。")
    _block(f"效率复盘没有量化：「消耗」要写总耗时、模型/工具耗时占比和 token。{measured}"
           "低效点要注明估计浪费的时间（完整报告：python scripts/session_stats.py）。")


if __name__ == "__main__":
    main()
