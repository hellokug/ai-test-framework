# Teaching Notes

- owner = 项目管理者（CLAUDE.md 教学总纲）：讲设计思路与取舍，不教语言基础；
  按四段式（做什么 / 为什么 / 什么模式 / 改哪里）组织讲解。
- 中文授课。代码示例一律引用真实文件与行号（`aite/functional/harness.py` 等），
  禁止虚构 API。
- 反馈闭环用项目现成命令：`.venv/Scripts/python.exe -m pytest
  tests/unit/test_functional_cases.py`（addopts 已带 `-q -n4`，命令行别再加
  `-q`；单测跑红先读校验器源码再改，别靠猜）。
- 2026-11-12 首课：新增断言（0001-add-new-assertion）。MISSION.md 是推断稿，
  待 owner 确认；learning-records 尚未建——等 owner 完成练习/自测有证据后再记。
