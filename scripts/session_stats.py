"""会话耗时与 token 量化：效率复盘的取数工具（AGENTS.md「效率复盘」）。

数据源是 ZCode 本地库 ~/.zcode/cli/db/db.sqlite（只读）：turn_usage（每轮 = 一次提问到
最终回复）、model_usage（每次模型请求的耗时、首 token 等待、token）、tool_usage（每次工具
调用的耗时、退出码）、part（工具入参，用来给慢调用配说明）。

用法::

    python scripts/session_stats.py                  # 当前会话的当前（或最近）一轮，完整报告
    python scripts/session_stats.py --brief          # 只出复盘「消耗」行
    python scripts/session_stats.py --all            # 当前会话全部轮次 + 合计
    python scripts/session_stats.py --session sess_xxx --turn 2

当前会话识别：--session > 环境变量 ZCODE_SESSION_ID（钩子进程里有）> 正在运行、命令含
session_stats 的那次 Bash 调用所属会话 > 本目录最近活跃的会话。
进行中的轮次还没有 turn_usage 汇总行，按 model_usage / tool_usage 现算，截止到此刻。
墙钟 = 模型请求耗时 + 工具耗时（并行调用按区间合并）+ 其他（钩子、调度、等待等）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
TOP_N = 5
RUNNING_WINDOW_MS = 15 * 60 * 1000   # 找「正在运行的本脚本调用」只看最近 15 分钟的 part


@dataclass
class Turn:
    session_id: str
    turn_id: str
    index: int
    total: int
    start: int
    end: int
    in_progress: bool
    models: list[sqlite3.Row]   # 主流程请求（query_source=main_turn），串行，构成模型耗时
    extras: list[sqlite3.Row]   # 子代理 / 标题生成等：子代理耗时已含在 Agent 工具耗时里
    tools: list[sqlite3.Row]

    @property
    def wall_ms(self) -> int:
        return max(self.end - self.start, 0)


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def resolve_session(con: sqlite3.Connection, explicit: str | None, marker: str = "session_stats") -> str:
    """marker：调用方脚本名——正在运行、命令里含它的那次 Bash 调用就属于当前会话。"""
    if explicit:
        return explicit
    env = os.environ.get("ZCODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if env:
        return env
    since = int(time.time() * 1000) - RUNNING_WINDOW_MS
    row = con.execute(
        "select session_id from part where time_updated >= ? and data like '%\"status\":\"running\"%'"
        " and data like ? order by time_updated desc limit 1", (since, f"%{marker}%")).fetchone()
    if row:
        return row["session_id"]
    row = con.execute("select id from session where directory = ? order by time_updated desc limit 1",
                      (str(Path.cwd()),)).fetchone()
    if not row:
        raise SystemExit("找不到当前会话：请用 --session sess_xxx 指定")
    return row["id"]


def load_turns(con: sqlite3.Connection, session_id: str) -> list[Turn]:
    order = con.execute(
        "select turn_id, min(started_at) s from model_usage where session_id = ? and turn_id is not null"
        " and query_source = 'main_turn' group by turn_id order by s", (session_id,)).fetchall()
    summaries = {r["turn_id"]: r for r in con.execute(
        "select * from turn_usage where session_id = ?", (session_id,))}
    now = int(time.time() * 1000)
    turns = []
    for index, row in enumerate(order, 1):
        tid = row["turn_id"]
        requests = con.execute("select * from model_usage where session_id = ? and turn_id = ?"
                               " order by started_at", (session_id, tid)).fetchall()
        tools = con.execute("select * from tool_usage where session_id = ? and turn_id = ?"
                            " order by started_at", (session_id, tid)).fetchall()
        summary = summaries.get(tid)
        if summary and summary["completed_at"]:
            start, end, live = summary["started_at"], summary["completed_at"], False
        else:
            start, end, live = row["s"], now, True
        turns.append(Turn(session_id, tid, index, len(order), start, end, live,
                          [r for r in requests if r["query_source"] == "main_turn"],
                          [r for r in requests if r["query_source"] != "main_turn"], tools))
    return turns


def union_ms(intervals: list[tuple[int, int]]) -> int:
    """并行工具调用的区间合并后的总时长。"""
    total, cur_start, cur_end = 0, None, None
    for start, end in sorted(intervals):
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                total += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    return total + (cur_end - cur_start if cur_end is not None else 0)


def dur(ms: float) -> str:
    if ms >= 60_000:
        return f"{ms / 60_000:.1f} 分"
    return f"{ms / 1000:.1f} 秒"


def tok(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def pct(part: float, whole: float) -> str:
    return f"{part * 100 / whole:.0f}%" if whole else "-"


def figures(turn: Turn) -> dict:
    model_ms = sum(m["duration_ms"] or 0 for m in turn.models)
    tool_ms = union_ms([(t["started_at"], t["completed_at"]) for t in turn.tools
                        if t["started_at"] and t["completed_at"]])
    inputs = [m["input_tokens"] or 0 for m in turn.models]
    single = [m for m in turn.models if (m["tool_call_count"] or 0) == 1]
    return {
        "model_ms": model_ms,
        "ttft_ms": sum(m["time_to_first_token_ms"] or 0 for m in turn.models),
        "tool_ms": tool_ms,
        "other_ms": max(turn.wall_ms - model_ms - tool_ms, 0),
        "input": sum(inputs),
        "cache_read": sum(m["cache_read_input_tokens"] or 0 for m in turn.models),
        "output": sum(m["output_tokens"] or 0 for m in turn.models),
        "ctx_first": inputs[0] if inputs else 0,
        "ctx_last": inputs[-1] if inputs else 0,
        "single_n": len(single),
        "single_ms": sum(m["duration_ms"] or 0 for m in single),
        "tool_out_bytes": sum(t["output_bytes"] or 0 for t in turn.tools),
        "tool_errors": sum(1 for t in turn.tools if t["error_type"] or (t["exit_code"] or 0) != 0),
    }


def brief(turn: Turn) -> str:
    f = figures(turn)
    state = "（进行中，截至此刻）" if turn.in_progress else ""
    return (f"耗时 {dur(turn.wall_ms)}{state}：模型 {dur(f['model_ms'])}（{pct(f['model_ms'], turn.wall_ms)}）"
            f"｜工具 {dur(f['tool_ms'])}（{pct(f['tool_ms'], turn.wall_ms)}）｜其他 {dur(f['other_ms'])}；"
            f"模型请求 {len(turn.models)} 次（只发 1 个工具调用的 {f['single_n']} 次，"
            f"共 {dur(f['single_ms'])}）、工具调用 {len(turn.tools)} 次；token 输入 {tok(f['input'])}"
            f"（缓存命中 {pct(f['cache_read'], f['input'])}）/ 输出 {tok(f['output'])}，"
            f"单次上下文 {tok(f['ctx_first'])}→{tok(f['ctx_last'])}")


def tool_label(con: sqlite3.Connection, turn: Turn, call_id: str) -> str:
    row = con.execute("select data from part where session_id = ? and data like ? limit 1",
                      (turn.session_id, f'%"callID":"{call_id}"%')).fetchone()
    try:
        args = json.loads(row["data"])["state"]["input"] if row else {}
    except (KeyError, TypeError, ValueError):
        args = {}
    for key in ("description", "title", "file_path", "skill", "prompt", "command"):
        value = args.get(key) if isinstance(args, dict) else None
        if isinstance(value, str) and value.strip():
            text = value.strip().splitlines()[0]
            return text if key != "file_path" else Path(text).name
    return ""


def at(turn: Turn, ms: int) -> str:
    return f"+{(ms - turn.start) / 60_000:.1f}分"


def report(con: sqlite3.Connection, turn: Turn) -> str:
    f = figures(turn)
    clock = time.strftime("%m-%d %H:%M", time.localtime(turn.start / 1000))
    durations = sorted(m["duration_ms"] or 0 for m in turn.models)
    p90 = durations[int(len(durations) * 0.9) - 1] if len(durations) >= 10 else (durations[-1] if durations else 0)
    lines = [
        f"会话 {turn.session_id[:13]}… 第 {turn.index}/{turn.total} 轮  开始 {clock}"
        f"  {'进行中' if turn.in_progress else '已完成'}",
        brief(turn),
        f"  模型单次：中位 {dur(statistics.median(durations)) if durations else '-'}，P90 {dur(p90)}，"
        f"最长 {dur(durations[-1]) if durations else '-'}；首 token 等待合计 {dur(f['ttft_ms'])}"
        f"（占模型时间 {pct(f['ttft_ms'], f['model_ms'])}）；工具报错 {f['tool_errors']} 次",
        "按工具类型（次数 / 合计 / 最长）：",
    ]
    by_tool: dict[str, list[int]] = {}
    for t in turn.tools:
        by_tool.setdefault(t["tool_name"], []).append(t["duration_ms"] or 0)
    for name, values in sorted(by_tool.items(), key=lambda kv: -sum(kv[1])):
        lines.append(f"  {name:24s} {len(values):3d} 次  {dur(sum(values)):>8s}  {dur(max(values)):>8s}")
    lines.append(f"最慢的 {TOP_N} 次工具调用：")
    for t in sorted(turn.tools, key=lambda r: -(r["duration_ms"] or 0))[:TOP_N]:
        lines.append(f"  {dur(t['duration_ms'] or 0):>8s}  {at(turn, t['started_at'])}  {t['tool_name']}"
                     f"  {tool_label(con, turn, t['tool_call_id'])[:60]}")
    lines.append(f"最慢的 {TOP_N} 次模型请求（耗时 / 首 token / 输出 / 上下文）：")
    for m in sorted(turn.models, key=lambda r: -(r["duration_ms"] or 0))[:TOP_N]:
        lines.append(f"  {dur(m['duration_ms'] or 0):>8s}  {at(turn, m['started_at'])}"
                     f"  首token {dur(m['time_to_first_token_ms'] or 0)}  输出 {tok(m['output_tokens'] or 0)}"
                     f"  上下文 {tok(m['input_tokens'] or 0)}")
    lines.append(f"撑大上下文的工具输出（本轮合计 {f['tool_out_bytes'] / 1024:.0f}KB，前 {TOP_N}）：")
    for t in sorted(turn.tools, key=lambda r: -(r["output_bytes"] or 0))[:TOP_N]:
        lines.append(f"  {(t['output_bytes'] or 0) / 1024:6.1f}KB  {t['tool_name']}"
                     f"  {tool_label(con, turn, t['tool_call_id'])[:60]}")
    if turn.extras:
        lines.append(f"另有子代理 / 标题生成请求 {len(turn.extras)} 次（子代理耗时已含在 Agent 工具耗时里）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="会话耗时与 token 量化（效率复盘取数）")
    parser.add_argument("--session", help="会话 id（默认自动识别当前会话）")
    parser.add_argument("--turn", type=int, help="第几轮（1 起；默认最近一轮）")
    parser.add_argument("--all", action="store_true", help="全部轮次 + 合计")
    parser.add_argument("--brief", action="store_true", help="只出复盘「消耗」行")
    args = parser.parse_args(argv)
    con = connect()
    turns = load_turns(con, resolve_session(con, args.session))
    if not turns:
        raise SystemExit("该会话没有模型请求记录")
    if args.all:
        for turn in turns:
            print(f"第 {turn.index} 轮  {brief(turn)}")
        wall = sum(t.wall_ms for t in turns)
        print(f"合计 {len(turns)} 轮：耗时 {dur(wall)}，模型请求 {sum(len(t.models) for t in turns)} 次，"
              f"工具调用 {sum(len(t.tools) for t in turns)} 次，"
              f"token 输入 {tok(sum(figures(t)['input'] for t in turns))}")
        return 0
    turn = turns[(args.turn - 1) if args.turn else -1]
    print(brief(turn) if args.brief else report(con, turn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
