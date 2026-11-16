"""Stop hook 门禁：本轮改过代码必须在最终回复给「四项契约交付」
（owner 2026-11-16 改约：教学四段式退役——owner 保的是项目级掌握
（契约面/风险面/数据流/依赖边），模块内部实现教学改第 5 条点名制）。

由 ``.zcode/config.json`` 注册到 ZCode 的 Stop 事件（process 类型）。判定：

- 证据源与 stop_efficiency_gate 一致：最终回复优先 Stop 载荷 responseText；
  「本轮是否动过代码」从 transcript_path / rollout（model-io JSONL）取——
  最后一条 user 消息之后的 Write/Edit/MultiEdit 工具调用，其 file_path 后缀
  属于 CODE_SUFFIXES 即视为改码（.md 文档不算）。
- 改码且回复缺 DELIVERY 四项（【契约摘要】【风险点位清单】【数据流简述】
  【依赖变更】）任一项、也无 TEACH_SKIP_MARKER（``<!--teach-skip-->``）→
  exit 2：stderr 列出缺的项与涉及的代码文件，提示按 CLAUDE.md 交付契约补齐
  （四项在前、代码在后；代码内风险行加 RISK 注释）。
- fail-open：读不到证据、没动代码 → exit 0。

手工验证::

    echo '{"responseText": "done"}' | python scripts/hooks/stop_teach_gate.py   # 无证据 exit 0
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TAIL_CHARS = 60000
CODE_SUFFIXES = {".py", ".js", ".ts", ".cjs", ".mjs", ".html", ".css"}
TEACH_SKIP_MARKER = "<!--teach-skip-->"
# 四项契约交付（owner 2026-11-16 改约）：最终回复缺任一标记即拦，全部命中才放行
DELIVERY = ("【契约摘要】", "【风险点位清单】", "【数据流简述】", "【依赖变更】")
# Write/Edit/MultiEdit 的工具调用 → 捕获 file_path（file_path 在 name 后 300 字符内）
TOOL_CALL = re.compile(
    r'"name"\s*:\s*"(?:Write|Edit|MultiEdit)".{0,300}?"file_path"\s*:\s*"([^"]+)"', re.S)
ROLLOUT_DIR = Path.home() / ".zcode" / "cli" / "rollout"


def _release() -> None:
    sys.exit(0)


def _block(reason: str) -> None:
    print(reason, file=sys.stderr)
    sys.exit(2)


def _missing_items(text: str) -> list[str]:
    return [item for item in DELIVERY if item not in text]


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


def _rollout_path(session_id: str) -> Path | None:
    candidate = ROLLOUT_DIR / f"model-io-{session_id}.jsonl"
    if candidate.exists():
        return candidate
    matches = sorted(ROLLOUT_DIR.glob(f"model-io-*{session_id}*.jsonl"))
    return matches[-1] if matches else None


def _turn_raw(data: dict) -> str:
    """本轮所在的原始 JSONL 片段来源：transcript 优先，rollout 兜底；取不到为空。"""
    transcript = data.get("transcript_path") or data.get("transcriptPath")
    if transcript:
        try:
            return Path(str(transcript)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    session_id = data.get("session_id") or data.get("sessionId")
    rollout = _rollout_path(str(session_id)) if session_id else None
    if rollout:
        try:
            return rollout.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return ""


def _last_user_tail(raw: str) -> str:
    """最后一条 user 消息之后的原始片段（本轮工具调用都在这段里）。"""
    boundary = raw[-TAIL_CHARS:]
    matches = list(re.finditer(r'"role"\s*:\s*"user"', boundary))
    return boundary[matches[-1].start():] if matches else boundary


def _code_files(raw: str) -> list[str]:
    """本轮 Write/Edit/MultiEdit 触及的代码文件（去重保序；.md 等非代码不算）。"""
    tail = _last_user_tail(raw)
    seen: list[str] = []
    for match in TOOL_CALL.finditer(tail):
        path = match.group(1).replace("\\\\", "\\")   # JSON 转义还原
        if Path(path).suffix.lower() in CODE_SUFFIXES and path not in seen:
            seen.append(path)
    return seen


def main() -> None:
    data = _stdin()
    if data.get("stop_hook_active") or data.get("stopHookActive"):
        _release()
    text = str(data.get("responseText") or data.get("last_assistant_message")
               or data.get("responsePreview") or "")
    raw = _turn_raw(data)
    if not raw:
        _release()                                   # 证据不全 fail-open
    code_files = _code_files(raw)
    missing = _missing_items(text)
    if not code_files or TEACH_SKIP_MARKER in text or not missing:
        _release()
    shown = "、".join(Path(p).name for p in code_files[:6])
    extra = f" 等共 {len(code_files)} 个" if len(code_files) > 6 else ""
    _block(f"交付四项缺失（缺 {'、'.join(missing)}）：本轮改动过代码文件"
           f"（{shown}{extra}）。按 CLAUDE.md 交付契约补齐，四项在前、代码在后："
           "【契约摘要】（对外函数/接口的入参/返回/异常，标新增/修改/不变）→"
           "【风险点位清单】（DB 写入/网络请求/全局状态/异常捕获/删除/复杂分支，"
           "文件:行号+函数+一句话）→【数据流简述】（≤3 行）→【依赖变更】（import "
           "增删改+跨层调用）；代码内风险行加 # RISK:（Python）或 // RISK:（JS）"
           "注释。琐碎修复（错字/一行日志等）可加 <!--teach-skip--> 显式跳过。")


if __name__ == "__main__":
    main()
