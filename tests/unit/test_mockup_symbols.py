"""mockup_symbols 守护检查回归：window 运行期改写口径、同体函数检测、版本一致性。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "mockup_symbols", Path(__file__).resolve().parents[2] / "scripts" / "mockup_symbols.py")
ms = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ms   # dataclass 装饰器执行期按 __module__ 回查 sys.modules，先注册再 exec
_SPEC.loader.exec_module(ms)


def _page(tmp_path: Path, name: str, html: str) -> Path:
    p = tmp_path / name
    p.write_text(html, encoding="utf-8")
    return p


def test_runtime_window_write_visible_but_not_clash(tmp_path):
    # 函数内缩进的 window.X = 运行期改写：查询可见（window.set），但不与列 0 声明算覆盖
    html = ("<script>function boot() { window.FOO = 1; }</script>"
            "<script>window.FOO = { live: true };</script>")
    page = _page(tmp_path, "p.html", html)
    assert ms.symbol_issues(page) == []
    kinds = [d.kind for s in ms.page_sources(page) for d in ms.definitions(s) if d.name == "FOO"]
    assert kinds == ["window.set", "window"]


def test_cross_script_function_override_flagged(tmp_path):
    html = ("<script>function render() { return 1; }</script>"
            "<script>function render() { return 2; }</script>")
    issues = ms.symbol_issues(_page(tmp_path, "p.html", html))
    assert len(issues) == 1 and "[跨脚本覆盖]" in issues[0]


def test_identical_body_flagged_across_declaration_forms(tmp_path):
    body = "{ const x = a || 0; return { total: x * 2, label: `档级 ${x}`, ok: x > 0 && x < 100 }; }"
    html = ("<script>function heavy(a) " + body + "</script>"
            "<script>const heavy2 = (a) => " + body + "</script>")
    issues = ms.identical_body_issues(_page(tmp_path, "p.html", html))
    assert len(issues) == 1 and "[同体函数]" in issues[0]
    assert "heavy(" in issues[0] and "heavy2(" in issues[0]


def test_identical_body_skips_short_delegates_and_data_objects(tmp_path):
    # 薄委托（<60 字符）与 const 数据对象（无 =>）不参与同体比较
    html = ("<script>const alias = escHtml;</script>"
            "<script>const CAPS = { api: 'API 契约', agent: 'Agent 能力', rag: 'RAG 检索' };</script>"
            "<script>const CAPS2 = { api: 'API 契约', agent: 'Agent 能力', rag: 'RAG 检索' };</script>")
    assert ms.identical_body_issues(_page(tmp_path, "p.html", html)) == []


def test_bodyless_declarations_do_not_swallow_next_body(tmp_path):
    # 数据数组 / 一行箭头没有函数体：扫描止于自身分号，不得吞并后续声明的体而误报同体
    html = ("<script>"
            "const DS = [ [1, 'a'], [2, 'b'] ];\n"
            "const DS_ROWS = DS.map(d => ({ type: d[0], name: d[1] }));\n"
            "</script>"
            "<script>"
            "const hit = (sc, x) => !kw || (sc + '.' + x[0]).toLowerCase().includes(kw);\n"
            "function pick() { const vis = list(); return vis.map(v => v + '!').join(','); }\n"
            "</script>")
    assert ms.identical_body_issues(_page(tmp_path, "p.html", html)) == []


def test_version_inconsistency_flagged(tmp_path):
    (tmp_path / "a.js").write_text("const x = 1;", encoding="utf-8")
    p1 = _page(tmp_path, "p1.html", '<script src="a.js?v=1"></script>')
    p2 = _page(tmp_path, "p2.html", '<script src="a.js?v=2"></script>')
    issues = ms.version_issues([p1, p2])
    assert len(issues) == 1 and "[资产版本不一致]" in issues[0]
