"""opt_audit（优化点自查）纯函数回归：账本解析、done 前缀匹配、陈旧项探测、节统计。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("opt_audit", ROOT / "scripts" / "opt_audit.py")
oa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oa)

LEDGER = (
    "# 效率优化台账\n\n| 日期 | 低效点 | 建议动作 | 状态 |\n|---|---|---|---|\n"
    "| 2026-11-08 | 陈旧候选 | 固化 scripts/widgets.py 一条龙 | open |\n"
    "| 2026-11-09 | 缺文件不报 | 固化 scripts/ghost.py：起服+灌数 | open |\n"
    "| 2026-11-10 | 已关账带说明 | 固化 scripts/widgets2.py | done（2026-11-16 核对：已落地） |\n"
)


def test_is_done_prefix_matching():
    assert oa.is_done("done")
    assert oa.is_done("done（2026-11-16 核对：已落地）")
    assert oa.is_done("DONE")
    assert oa.is_done("已完成（2026-11-11 补记）")
    assert not oa.is_done("open")
    assert not oa.is_done("进行中")


def test_stale_open_only_flags_existing_artifacts(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    (docs / "efficiency-log.md").write_text(LEDGER, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "widgets.py").write_text("x = 1\n", encoding="utf-8")
    stale = oa.stale_open(tmp_path)
    assert [s[0] for s in stale] == ["2026-11-08"]
    assert "scripts/widgets.py" in stale[0][1]


def test_agents_stats_counts_sections(tmp_path: Path):
    md = tmp_path / "AGENTS.md"
    md.write_text("前言两行\n还一行\n## 甲节\n甲1\n甲2\n甲3\n## 乙节\n乙1\n", encoding="utf-8")
    total, sections = oa.agents_stats(md)
    assert total == 8
    assert sections[0] == ("甲节", 3)
    assert sections[1] == ("(前言)", 2)
    assert sections[2] == ("乙节", 1)


def test_inventory_lists_scripts_and_skills(tmp_path: Path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "widgets.py").write_text(
        '"""小工具：做某事。"""\nx = 1\n', encoding="utf-8")
    skill = tmp_path / ".agents" / "skills" / "do-it" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: do-it\ndescription: 干活的 runbook\n---\n\n正文\n", encoding="utf-8")
    inv = oa.inventory(tmp_path)
    assert "scripts/widgets.py: 小工具：做某事。" in inv
    assert ".agents/skills/do-it: 干活的 runbook" in inv
