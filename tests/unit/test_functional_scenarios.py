"""内置功能场景收集验证（离线：--collect-only，不发起任何模型请求）。"""

import re
import subprocess
import sys
from pathlib import Path

import aite.functional

SCENARIOS_DIR = Path(aite.functional.__file__).parent / "scenarios"
EXPECTED = {"basic_chat": 4, "structured_output": 4, "tool_calling": 3, "long_context": 3}


def _collect(target: Path) -> int:
    """跑 pytest --collect-only，返回收集到的用例数（pytest 8 -q 输出按文件 `path: N`）。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(target), "--collect-only", "-q",
         "-p", "aite.functional.harness"],
        capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return sum(int(n) for n in re.findall(r": (\d+)\s*$", proc.stdout, re.M))


def test_harness_plugin_loadable_without_env():
    """harness 插件可加载，且环境变量缺失不影响收集（请求时才失败）。"""
    assert _collect(SCENARIOS_DIR / "basic_chat") == 4


def test_all_scenarios_collectable():
    """4 个内置场景全部可收集，共 14 例。"""
    assert _collect(SCENARIOS_DIR) == 14
    for name, n in EXPECTED.items():
        assert _collect(SCENARIOS_DIR / name) == n, name
