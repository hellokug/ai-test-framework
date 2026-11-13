"""静态稿全局符号查询与一致性检查（web-ui/mockups/v1）。

静态稿页面 = 内联脚本 + 后加载的对接层（functional2/common2 → api32.js，
accuracy2 → api-acc3.js），经典脚本共享全局作用域：后加载的同名 function 静默覆盖
先加载的，同名 const/let 直接 SyntaxError。只在页面文件里 grep 会误判「函数缺失」，
写出被覆盖的死代码（2026-11-09 复盘：nsFillRowFromData 重复实现事故）。

用法::

    python scripts/mockup_symbols.py functional2 nsFillRowFromData nsRevealRow
        # 按加载顺序列出每个符号的全部顶层定义并标出生效处；有符号未找到 → exit 1
    python scripts/mockup_symbols.py --check
        # 全部页面：跨脚本同名覆盖、同脚本重复定义、
        # 同一本地资产在各页面的 ?v= 不一致 → 逐条列出并 exit 1

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
    for match in _DECL.finditer(source.text):
        name = match.group("fn") or match.group("binding") or match.group("win")
        kind = "function" if match.group("fn") else (match.group("kw") or "window")
        found.append(Definition(name, kind, source,
                                source.line0 + source.text.count("\n", 0, match.start())))
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
        if len(defs) < 2:
            continue
        if _lexical_clash(defs):
            issues.append(f"[跨脚本重复声明] {page.name}: {name} 定义于 {_where(defs)}"
                          f" —— const/let/class 重名，{defs[-1].source.file} 整体 SyntaxError 不执行")
        elif len({d.source for d in defs}) > 1:
            issues.append(f"[跨脚本覆盖] {page.name}: {name} 定义于 {_where(defs)}"
                          f" —— 生效的是最后执行的 {defs[-1].source.file}，其余是死代码")
        else:
            issues.append(f"[同脚本重复] {page.name}: {name} 定义于 {_where(defs)}")
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
    issues = [i for page in pages for i in symbol_issues(page)] + version_issues(pages)
    for issue in issues:
        print(issue)
    print(f"静态稿检查：{len(issues)} 个问题" if issues
          else f"静态稿检查通过（{len(pages)} 个页面）：无跨脚本覆盖、重复定义、资产版本不一致")
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
