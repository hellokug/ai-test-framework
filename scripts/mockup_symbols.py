"""静态稿全局符号查询与一致性检查（web-ui/mockups/v1）。

静态稿页面 = 内联脚本 + 后加载的对接层（functional2/common2 → api32.js，
accuracy2 → api-acc3.js），经典脚本共享全局作用域：后加载的同名 function 静默覆盖
先加载的，同名 const/let 直接 SyntaxError。只在页面文件里 grep 会误判「函数缺失」，
写出被覆盖的死代码（2026-11-09 复盘：nsFillRowFromData 重复实现事故）。

用法::

    python scripts/mockup_symbols.py functional2 nsFillRowFromData nsRevealRow
        # 按加载顺序列出每个符号的全部顶层定义并标出生效处；有符号未找到 → exit 1
    python scripts/mockup_symbols.py --check
        # 全部页面：跨脚本同名覆盖、同脚本重复定义、同页逐字相同函数体（异名同义
        # 拷贝）、同一本地资产在各页面的 ?v= 不一致 → 逐条列出并 exit 1

识别口径：行首（列 0）的 function / async function / const / let / var / class 与
``window.X =`` 赋值视为顶层定义（静态稿惯例顶层不缩进）；缩进的嵌套定义不计。
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOCKUP_DIR = ROOT / "web-ui" / "mockups" / "v1"

_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
_ASSET_REF = re.compile(r'<(?:script|link)\b[^>]*?\b(?:src|href)="([^"]+)"', re.I)
_NON_JS_TYPE = re.compile(r'\btype="(?!module"|text/javascript")[^"]*"', re.I)
_REMOTE = re.compile(r"^(?:[a-z]+:|//)", re.I)
_DECL = re.compile(
    r"^(?:(?:async\s+)?function\s*\*?\s*(?P<fn>[A-Za-z_$][\w$]*)\s*\("
    r"|(?P<kw>const|let|var|class)\s+(?P<binding>[A-Za-z_$][\w$]*)"
    r"|window\.(?P<win>[A-Za-z_$][\w$]*)\s*=(?!=))",
    re.M,
)
_WIN_ASSIGN = re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\s*=(?!=)")   # 任意缩进的运行期改写


@dataclass(frozen=True)
class Source:
    label: str   # 加载链展示名："api32.js" / "functional2.html 内联脚本@L409"
    file: str    # 定义位置用的文件名（内联脚本 = 页面本身）
    text: str
    line0: int   # 文本首行在所属文件中的行号


@dataclass(frozen=True)
class Definition:
    name: str
    kind: str    # function / const / let / var / class / window
    source: Source
    line: int

    @property
    def where(self) -> str:
        return f"{self.source.file}:L{self.line}"


def page_sources(page: Path) -> list[Source]:
    """页面的 JS 来源，按浏览器执行顺序；跳过 CDN 与非 JS 类型（如 text/plain 数据块）。"""
    html = page.read_text(encoding="utf-8")
    sources = []
    for match in _SCRIPT.finditer(html):
        attrs, body = match.group(1), match.group(2)
        if _NON_JS_TYPE.search(attrs):
            continue
        line0 = html.count("\n", 0, match.start()) + 1
        src = re.search(r'\bsrc="([^"]+)"', attrs)
        if not src:
            sources.append(Source(f"{page.name} 内联脚本@L{line0}", page.name, body, line0))
            continue
        ref = re.split(r"[?#]", src.group(1))[0]
        path = page.parent / ref
        if not _REMOTE.match(ref) and path.is_file():
            sources.append(Source(ref, ref, path.read_text(encoding="utf-8"), 1))
    return sources


def definitions(source: Source) -> list[Definition]:
    found = []
    seen_win: set[tuple[str, int]] = set()
    for match in _DECL.finditer(source.text):
        name = match.group("fn") or match.group("binding") or match.group("win")
        kind = "function" if match.group("fn") else (match.group("kw") or "window")
        line = source.line0 + source.text.count("\n", 0, match.start())
        if kind == "window":
            seen_win.add((name, line))
        found.append(Definition(name, kind, source, line))
    # 任意缩进的 window.X = 运行期改写也计入（查询口径完整：common2.html 函数内的
    # window.DS_FILTERS 曾不可见）；kind=window.set，不参与覆盖检查（见 symbol_issues）
    for match in _WIN_ASSIGN.finditer(source.text):
        name = match.group(1)
        line = source.line0 + source.text.count("\n", 0, match.start())
        if (name, line) not in seen_win:
            found.append(Definition(name, "window.set", source, line))
    return found


def _where(defs: list[Definition]) -> str:
    return "、".join(d.where for d in defs)


def _lexical_clash(defs: list[Definition]) -> bool:
    """const/let/class 跨经典脚本重名：后加载的脚本整体 SyntaxError，不是覆盖。"""
    return len({d.source for d in defs}) > 1 and any(d.kind in ("const", "let", "class") for d in defs)


def lookup(page: Path, names: list[str]) -> int:
    sources = page_sources(page)
    print(f"{page.name} 加载顺序：" + " → ".join(f"{i}.{s.label}" for i, s in enumerate(sources, 1)))
    defs = [d for s in sources for d in definitions(s)]
    missing = 0
    for name in names:
        hits = [d for d in defs if d.name == name]
        if not hits:
            missing += 1
            print(f"  {name}: 未找到顶层定义（局部变量、拼写错误或缩进的嵌套定义）")
            continue
        print(f"  {name}:")
        clash = _lexical_clash(hits)
        for d in hits:
            note = ""
            if clash:
                note = "  （重复的 const/let/class：后加载的脚本整体 SyntaxError 不执行）"
            elif len(hits) > 1:
                note = "  ← 生效（最后执行）" if d is hits[-1] else "  （被后者覆盖，死代码）"
            print(f"    {sources.index(d.source) + 1}.{d.where}  {d.kind}{note}")
    return 1 if missing else 0


def symbol_issues(page: Path) -> list[str]:
    by_name: dict[str, list[Definition]] = {}
    for source in page_sources(page):
        for d in definitions(source):
            by_name.setdefault(d.name, []).append(d)
    issues = []
    for name, defs in sorted(by_name.items()):
        # 运行期 window.X = 改写（window.set）不参与覆盖检查：内联 mock ← 对接层 live
        # 接管是本仓库惯例（DS_FILTERS），同脚本多次改写也是正常状态变更；只查列 0 声明
        decls = [d for d in defs if d.kind != "window.set"]
        if len(decls) < 2:
            continue
        if _lexical_clash(decls):
            issues.append(f"[跨脚本重复声明] {page.name}: {name} 定义于 {_where(decls)}"
                          f" —— const/let/class 重名，{decls[-1].source.file} 整体 SyntaxError 不执行")
        elif len({d.source for d in decls}) > 1:
            issues.append(f"[跨脚本覆盖] {page.name}: {name} 定义于 {_where(decls)}"
                          f" —— 生效的是最后执行的 {decls[-1].source.file}，其余是死代码")
        else:
            issues.append(f"[同脚本重复] {page.name}: {name} 定义于 {_where(decls)}")
    return issues


_IDENTICAL_MIN = 60   # 归一化函数体最小长度：放行薄委托与一行箭头（如 const nsEsc = escHtml）


def _func_text(text: str, start: int) -> tuple[str, str] | None:
    """返回（声明头, 函数体）：体 = 首个「()[]{} 深度 0 处的花括号」起至其配平 }（字符串
    字面量不参与配平）；深度 0 处遇 ; 而体未开即止（数据数组/一行箭头没有函数体，不得
    吞并后续声明）；无体返回 None。头 = 声明起点至体开括号（供 const 场景 => 探测）。"""
    depth = 0
    brace = 0
    open_at = -1
    state = None
    i = start
    while i < len(text):
        ch = text[i]
        if state:
            i += 2 if ch == "\\" else 1
            if ch == state:
                state = None
            continue
        if ch in "\"'`":
            state = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "{":
            if open_at < 0 and depth == 0:
                open_at = i
                brace = 1
            elif open_at >= 0:
                brace += 1
        elif ch == "}":
            if open_at >= 0:
                brace -= 1
                if brace == 0:
                    return text[start:open_at], text[open_at:i + 1]
        elif ch == ";" and open_at < 0 and depth == 0:
            return None   # 声明已终结仍无函数体
        i += 1
    return None


def identical_body_issues(page: Path) -> list[str]:
    """同页跨脚本逐字相同的函数体（异名同义拷贝）：后者多半是漏收敛的重复实现，
    演化时只改其中一份就分叉漂移（toast 时长参数漂移即前车之鉴）。"""
    by_body: dict[str, list[str]] = {}
    for source in page_sources(page):
        for match in _DECL.finditer(source.text):
            if match.group("kw") not in (None, "const"):
                continue
            name = match.group("fn") or match.group("binding")
            if not name:
                continue
            got = _func_text(source.text, match.start())
            if not got:
                continue
            head, body = got
            if match.group("kw") == "const" and "=>" not in head:
                continue   # const 数据对象（无 => 的花括号字面量）不是函数，不比体
            norm = re.sub(r"\s+", " ", body).strip()
            if len(norm) < _IDENTICAL_MIN:
                continue
            line = source.line0 + source.text.count("\n", 0, match.start())
            by_body.setdefault(norm, []).append(f"{name}({source.file}:L{line})")
    issues = []
    for hits in sorted(by_body.values()):
        if len(hits) > 1:
            issues.append(f"[同体函数] {page.name}: {'、'.join(hits)}"
                          " —— 函数体逐字相同（异名同义拷贝），收敛到共享实现后删本地拷贝")
    return issues


def version_issues(pages: list[Path]) -> list[str]:
    """同一本地资产被多个页面引用时 ?v= 必须一致（改共享文件后各页统一 bump）。"""
    seen: dict[str, dict[str, str]] = {}
    for page in pages:
        for ref in _ASSET_REF.findall(page.read_text(encoding="utf-8")):
            path, _, query = ref.partition("?")
            if _REMOTE.match(path) or path.endswith(".html"):
                continue
            version = re.search(r"(?:^|&)v=([^&#]+)", query)
            seen.setdefault(path, {})[page.name] = version.group(1) if version else "（无）"
    issues = []
    for asset, per_page in sorted(seen.items()):
        if len(set(per_page.values())) > 1:
            detail = "，".join(f"{p} v={v}" for p, v in sorted(per_page.items()))
            issues.append(f"[资产版本不一致] {asset}: {detail} —— 统一 bump 到同一版本号")
    return issues


def check(pages: list[Path]) -> int:
    issues = [i for page in pages for i in symbol_issues(page) + identical_body_issues(page)]
    issues += version_issues(pages)
    for issue in issues:
        print(issue)
    print(f"静态稿检查：{len(issues)} 个问题" if issues
          else f"静态稿检查通过（{len(pages)} 个页面）：无跨脚本覆盖、重复定义、同体函数、资产版本不一致")
    return 1 if issues else 0


def resolve_page(arg: str) -> Path:
    direct = Path(arg)
    if direct.is_file():
        return direct
    candidate = MOCKUP_DIR / (arg if arg.endswith(".html") else f"{arg}.html")
    if candidate.is_file():
        return candidate
    raise SystemExit(f"找不到页面 {arg}（按文件名在 {MOCKUP_DIR} 下查找）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="静态稿全局符号查询与一致性检查")
    parser.add_argument("page", nargs="?", help="页面，如 functional2 或 functional2.html")
    parser.add_argument("names", nargs="*", help="要查的全局符号")
    parser.add_argument("--check", action="store_true", help="检查全部页面")
    args = parser.parse_args(argv)
    if args.check:
        return check(sorted(MOCKUP_DIR.glob("*.html")))
    if not args.page or not args.names:
        parser.error("需要「页面 + 符号」，或 --check")
    return lookup(resolve_page(args.page), args.names)


if __name__ == "__main__":
    sys.exit(main())
