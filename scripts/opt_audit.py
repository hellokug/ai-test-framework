"""优化点自查（定期合并收敛用）：账本陈旧项 + AGENTS.md 体积 + scripts/skills 盘点。

owner 2026-11-16 要求：落地的小优化点太多会分散注意力，隔一段时间自查合并收敛。
本脚本只出信息性报告（退出码恒 0，不是门禁），配合每周一定时自动化：先跑本报告，
再做关账（人工核对产物确实覆盖后改 done）与机械合并（语义取舍留 owner 决策）。

用法：.venv/Scripts/python.exe scripts/opt_audit.py
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DONE_STATUSES = ("done", "closed", "已完成")
_PATH_RE = re.compile(r"[A-Za-z0-9_\-./]+\.(?:py|cjs|mjs|js|html|css)")
_CELL_SPLIT = re.compile(r"\s*\|\s*")


def ledger_rows(path: Path) -> list[tuple[str, str, str, str]]:
    """效率账本表格行：(日期, 低效点, 建议动作, 状态)，跳过表头/分隔行/正文。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip().replace("\\|", "|") for c in _CELL_SPLIT.split(line.strip().strip("|"))]
        if len(cells) >= 4 and cells[0][:4].isdigit():
            out.append((cells[0], cells[1], cells[2], cells[3]))
    return out


def is_done(status: str) -> bool:
    """状态列以 done/closed/已完成 开头即关账（兼容「done（2026-11-16 说明）」写法）。"""
    s = status.strip().lower()
    return any(s.startswith(d) for d in DONE_STATUSES)


def _mentioned_paths(text: str) -> list[str]:
    """文本里像仓库相对路径的 token（含 / 且扩展名可辨识，去重保序）。"""
    return list(dict.fromkeys(
        tok for tok in _PATH_RE.findall(text)
        if "/" in tok and not tok.startswith(("http://", "https://"))
    ))


def stale_open(root: Path) -> list[tuple[str, str, str]]:
    """open 行提及、且在仓库里真实存在的文件 → 疑似已落地未关账。

    # RISK: 文件存在 ≠ 落地完成（如 iab_sandbox 行要求的是加 detached 标记），
    # 只是「人工核对」信号，不是自动关账依据。
    """
    hits = []
    for date, what, action, status in ledger_rows(root / "docs" / "efficiency-log.md"):
        if is_done(status):
            continue
        paths = [p for p in _mentioned_paths(f"{what} {action}") if (root / p).exists()]
        if paths:
            hits.append((date, "、".join(paths), action))
    return hits


def agents_stats(path: Path) -> tuple[int, list[tuple[str, int]]]:
    """AGENTS.md 总行数（含标题行）+ 各 ## 节行数（降序），看注意力分散度。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    cur, n, sections = "(前言)", 0, []
    for ln in lines:
        if ln.startswith("## "):
            sections.append((cur, n))
            cur, n = ln[3:].strip(), 0
        else:
            n += 1
    sections.append((cur, n))
    sections.sort(key=lambda x: -x[1])
    return len(lines), sections


def _first_docline(path: Path) -> str:
    """py 取模块 docstring 首行；SKILL.md 取 frontmatter description 值。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    for ln in text.splitlines():
        s = ln.strip()
        if s.lower().startswith("description:"):
            return s[len("description:"):].strip()[:90]
    if path.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(text)) or ""
        except (SyntaxError, ValueError):
            return ""
        return doc.splitlines()[0][:90] if doc else ""
    return ""


def inventory(root: Path) -> list[str]:
    """scripts/*.py 与 .agents/skills/*/SKILL.md 一行盘点。"""
    out = [f"scripts/{f.name}: {_first_docline(f) or '-'}"
           for f in sorted((root / "scripts").glob("*.py"))]
    skills = root / ".agents" / "skills"
    if skills.is_dir():
        out += [f".agents/skills/{f.parent.name}: {_first_docline(f) or '-'}"
                for f in sorted(skills.glob("*/SKILL.md"))]
    return out


def main() -> int:
    print("== 优化点自查（信息性报告，非门禁）==")
    stale = stale_open(ROOT)
    print(f"-- 账本陈旧项（open 但提及文件已存在，人工核对后关账）：{len(stale)} 条")
    for date, paths, action in stale:
        print(f"  {date}  {paths}")
        print(f"    → {action[:80]}")
    total, sections = agents_stats(ROOT / "AGENTS.md")
    print(f"-- AGENTS.md：{total} 行 / {len(sections)} 节，最大三节：")
    for name, n in sections[:3]:
        print(f"  {n:4d} 行  {name[:60]}")
    inv = inventory(ROOT)
    print(f"-- 盘点：{len(inv)} 个（scripts + skills）")
    for line in inv:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
