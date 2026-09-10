"""函数长度审计：CLAUDE.md 原则 1（函数 ≤80 行）的机器回归检查。

用法：python scripts/audit_func_len.py [阈值]
超限函数打印并退出码 1；随 CI/提交前手动执行。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 80
ROOT = Path(__file__).resolve().parent.parent / "aite"


def main() -> int:
    over = []
    for path in sorted(ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            print(f"SYNTAX ERROR {path.as_posix()}: {exc}")
            return 1
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                if length > LIMIT:
                    over.append((length, path.as_posix(), node.name, node.lineno))
    if over:
        for length, loc, name, lineno in sorted(over, reverse=True):
            print(f"{length:4d}  {loc}:{lineno}  {name}")
        print(f"{len(over)} function(s) exceed {LIMIT} lines")
        return 1
    print(f"OK: all functions <= {LIMIT} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
