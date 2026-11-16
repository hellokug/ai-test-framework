"""收尾一键自检：门禁 + 按 diff 选受影响测试 + 重启判定。

把 AGENTS.md 三个散点（验证门禁命令速查 / 选测对照 / 改动收尾必答要不要重启）
合成一条命令：

    .venv/Scripts/python.exe scripts/preflight.py              # 门禁 + 选测建议 + 重启判定
    .venv/Scripts/python.exe scripts/preflight.py --run-tests  # 同上，并直接执行选出的 pytest
    .venv/Scripts/python.exe scripts/preflight.py --skip-tests # 只跑门禁
    .venv/Scripts/python.exe scripts/preflight.py --base HEAD~2

diff 基线缺省自动判：工作树有未提交改动（含未跟踪）比 HEAD；干净树比 HEAD~1
（刚提交完的一轮）。选测原理：ast 扫 aite/、web/ 全部 import 构图，改动模块取
反向闭包，命中闭包的 tests/ 文件入选；scripts/ 按文件名约定配对
tests/**/test_*<stem>*。from x import y 把符号也记账，映射偏保守——宁多选不漏
测；映射为空时回 AGENTS.md「选测对照」人工兜底。
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PKG_DIRS = ("aite", "web")
IGNORE_PREFIXES = (".tmp", ".venv", "__pycache__")


def _git(*args: str) -> str:
    # RISK: 子进程调 git，输出按 utf-8 解码，坏编码文件名 errors=replace 容错
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=False)
    return r.stdout.decode("utf-8", errors="replace")


def collect_changes(base: str | None) -> tuple[str, list[str]]:
    """返回 (基线描述, 改动文件列表)，含未跟踪文件，保持 git 输出顺序去重。"""
    untracked = [
        ln[3:] for ln in _git("status", "--porcelain").splitlines() if ln.startswith("?? ")
    ]
    untracked = [p for p in untracked if not p.startswith(IGNORE_PREFIXES)]
    if base is not None:
        files = _git("diff", base, "--name-only").splitlines() + untracked
        return f"{base} vs 工作树", list(dict.fromkeys(files))
    tracked = _git("diff", "HEAD", "--name-only").splitlines()
    if tracked or untracked:
        return "工作树 vs HEAD", list(dict.fromkeys(tracked + untracked))
    return "HEAD vs HEAD~1", _git("diff", "HEAD~1", "--name-only").splitlines()


def classify(paths: list[str]) -> dict[str, list[str]]:
    """按改动域分桶：包内 py / 测试 py / 脚本 py / 静态稿 / 文档 / 其他。"""
    b: dict[str, list[str]] = {
        "pkg_py": [], "tests_py": [], "scripts_py": [], "mockups": [], "docs": [], "other": [],
    }
    for p in paths:
        pp = PurePosixPath(p)
        parts = pp.parts
        if pp.suffix == ".py" and parts and parts[0] in PKG_DIRS:
            b["pkg_py"].append(p)
        elif pp.suffix == ".py" and parts[:1] == ("tests",):
            b["tests_py"].append(p)
        elif pp.suffix == ".py" and parts[:1] == ("scripts",):
            b["scripts_py"].append(p)
        elif parts[:2] == ("web-ui", "mockups") or parts[:2] == ("web-ui", "assets"):
            b["mockups"].append(p)
        elif parts[:1] == ("docs",) or pp.suffix == ".md":
            b["docs"].append(p)
        else:
            b["other"].append(p)
    return b


def module_name(rel: str) -> str | None:
    """仓库相对路径 → 模块名；aite/service/x.py → aite.service.x，__init__ 归到包名。"""
    pp = PurePosixPath(rel)
    if pp.suffix != ".py" or not pp.parts or pp.parts[0] not in PKG_DIRS:
        return None
    parts = list(pp.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or None


def scan_imports(path: Path, root: Path) -> set[str]:
    """该文件 import 的 aite.*/web.* 目标集合。

    # RISK: from x import y 会把符号 y 也记成模块候选，映射偏保守（宁多选不漏测）；
    # 坏编码/语法损坏的文件 ast 失败时返回空集跳过（少映射不崩）。
    """
    try:
        tree = ast.parse(path.read_bytes())
    except (SyntaxError, ValueError, OSError):
        return set()
    own = module_name(path.relative_to(root).as_posix())
    deps: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            deps.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and own:
                pkg = own if own.split(".")[-1] == "__init__" or "." not in own else own.rsplit(".", 1)[0]
                # 相对导入锚点：从所属包上溯 level-1 层
                parts = pkg.split(".")[: len(pkg.split(".")) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base:
                deps.add(base)
                deps.update(f"{base}.{alias.name}" for alias in node.names)
    return {d for d in deps if d.split(".")[0] in PKG_DIRS}


def build_import_graph(root: Path) -> dict[str, set[str]]:
    """扫 aite/、web/ 全部 py 的 import，得 模块 → 依赖模块集合。"""
    graph: dict[str, set[str]] = {}
    for pkg in PKG_DIRS:
        base = root / pkg
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            mod = module_name(f.relative_to(root).as_posix())
            if mod:
                graph[mod] = scan_imports(f, root)
    return graph


def reverse_closure(graph: dict[str, set[str]], seeds: set[str]) -> set[str]:
    """改动模块的反向依赖闭包：直接或经 aite/web 内部链引用改动模块的模块全体。"""
    affected = {s for s in seeds if s}
    frontier = set(affected)
    while frontier:
        nxt = {m for m, deps in graph.items() if m not in affected and deps & frontier}
        affected |= nxt
        frontier = nxt
    return affected


def affected_tests(root: Path, changed: list[str]) -> list[str]:
    """改动 → 受影响 tests/ 文件列表（仓库相对 posix 路径，已排序去重）。"""
    b = classify(changed)
    picked: set[str] = set(b["tests_py"])
    seeds = {m for p in b["pkg_py"] if (m := module_name(p))}
    tests_dir = root / "tests"
    if b["pkg_py"] and seeds:
        closure = reverse_closure(build_import_graph(root), seeds)
        if tests_dir.is_dir():
            for f in sorted(tests_dir.rglob("*.py")):
                rel = f.relative_to(root).as_posix()
                if rel not in picked and scan_imports(f, root) & closure:
                    picked.add(rel)
    if b["scripts_py"] and tests_dir.is_dir():
        for sp in b["scripts_py"]:
            stem = PurePosixPath(sp).stem
            picked.update(
                f.relative_to(root).as_posix()
                for f in tests_dir.rglob(f"test_*{stem}*.py")
            )
    return sorted(picked)


def gate_plan(mockups_changed: bool) -> list[tuple[str, list[str]]]:
    """门禁清单；静态稿改动才追加 mockup_symbols --check（选门禁）。"""
    py = sys.executable
    exe = ROOT / ".venv" / "Scripts" / "lint-imports.exe"
    lint = [str(exe)] if exe.exists() else [py, "-m", "lint_imports"]
    plan = [
        ("ruff", [py, "-m", "ruff", "check", "aite", "tests", "scripts"]),
        ("lint-imports", lint),
        ("audit_func_len", [py, "scripts/audit_func_len.py"]),
        ("arch_map", [py, "scripts/arch_map.py"]),
    ]
    if mockups_changed:
        plan.append(("mockup_symbols --check", [py, "scripts/mockup_symbols.py", "--check"]))
    return plan


def run_gates(plan: list[tuple[str, list[str]]]) -> list[tuple[str, bool, float, str]]:
    results = []
    for name, cmd in plan:
        t0 = time.monotonic()
        # RISK: 子进程执行门禁命令；arch_map 会再生架构图产物（写盘副作用，属例行门禁行为）
        r = subprocess.run(cmd, capture_output=True, cwd=ROOT, check=False)
        out = (r.stdout + b"\n" + r.stderr).decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        tail = lines[-1][:160] if lines else f"exit {r.returncode}"
        results.append((name, r.returncode == 0, time.monotonic() - t0, tail))
    return results


def restart_hint(b: dict[str, list[str]]) -> list[str]:
    hints = []
    if b["pkg_py"]:
        hints.append("aite/ 或 web/ 有改动 → 需重启 aite serve（uvicorn 无 --reload）")
    if b["mockups"]:
        hints.append("静态稿改动 → 无需重启，刷新页面即生效；共享 assets/api32 改过记得统一 bump ?v=")
    if not hints:
        hints.append("仅文档/脚本改动 → 无需重启任何服务")
    return hints


def main() -> int:
    ap = argparse.ArgumentParser(description="收尾一键：门禁 + 按 diff 选测 + 重启判定")
    ap.add_argument("--base", help="diff 基线 ref；缺省自动判（脏树比 HEAD，干净树比 HEAD~1）")
    ap.add_argument("--skip-tests", action="store_true", help="不做选测映射")
    ap.add_argument("--run-tests", action="store_true", help="直接执行选出的 pytest（默认只打印命令）")
    args = ap.parse_args()

    base_desc, files = collect_changes(args.base)
    b = classify(files)
    print(f"== preflight ==  diff 基线: {base_desc}，改动 {len(files)} 个文件")
    for f in (files if len(files) <= 12 else files[:12] + [f"... 共 {len(files)} 个"]):
        print("  ", f)

    print("-- 门禁 --")
    failed = 0
    for name, ok, secs, tail in run_gates(gate_plan(bool(b["mockups"]))):
        failed += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} ({secs:.1f}s){'' if ok else '  ' + tail}")

    tests = [] if args.skip_tests else affected_tests(ROOT, files)
    if tests:
        print("-- 受影响测试 --")
        print("  建议: .venv/Scripts/python.exe -m pytest " + " ".join(tests))
    elif not args.skip_tests:
        print("-- 受影响测试 --")
        print("  映射为空 → 查 AGENTS.md「选测对照」人工兜底")

    print("-- 重启判定 --")
    for h in restart_hint(b):
        print("  ", h)
    if b["mockups"]:
        print("   交互验证走 skill mockup-verify；api32/详情渲染改动可先 web-ui/mockups/render_check.cjs")

    if args.run_tests and tests:
        # RISK: 直接执行选出的 pytest，失败以退出码透传给调用方
        return subprocess.run([sys.executable, "-m", "pytest", *tests], cwd=ROOT).returncode
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
