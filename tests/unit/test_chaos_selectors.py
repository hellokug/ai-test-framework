"""注入对象选择引擎单测（§14.1 注入层）：match 四模式 / exclude / select 五策略。

对真实子进程解析（cmdline 唯一 token 定位），不 mock psutil——选择引擎是
注入正确性的第一道门，必须实弹。
"""

import os
import subprocess
import sys

import pytest

from aite.chaos.selectors import parse_process_spec, resolve_processes

TOKEN = "chaos-selector-demo"


BASE_PY = getattr(sys, "_base_executable", sys.executable)
# venv 的 python.exe 是启动器壳，会再拉起基础解释器——psutil 里一变二，
# 直接用基础解释器生成子进程保证一一对应。

def _spawn(token: str) -> subprocess.Popen:
    return subprocess.Popen(
        [BASE_PY, "-c", f"import time;time.sleep(60)  #{token}"])


@pytest.fixture(scope="module")
def children():
    procs = [_spawn(f"{TOKEN}-one"), _spawn(f"{TOKEN}-two"),
             _spawn(f"{TOKEN}-three")]
    yield procs
    for p in procs:
        p.terminate()


def _match(token: str) -> dict:
    return {"contains": token}


def test_parse_process_spec_rules():
    match, exclude, select = parse_process_spec(
        {"contains": "vllm", "exclude": [{"exact": "x"}],
         "select": {"mode": "all"}})
    assert match == {"contains": "vllm"}
    assert exclude == [{"exact": "x"}] and select == {"mode": "all"}
    with pytest.raises(ValueError, match="恰好一个匹配键"):
        parse_process_spec({"contains": "a", "exact": "b"})
    with pytest.raises(ValueError, match="恰好一个匹配键"):
        parse_process_spec({"foo": "a"})


def test_contains_and_exclude(children):
    all_pids = resolve_processes(_match(TOKEN), select={"mode": "all"})
    assert set(p.pid for p in children) == set(all_pids)
    pids = resolve_processes(_match(TOKEN), exclude=[{"contains": "two"}])
    assert children[1].pid not in pids and len(pids) == 2


def test_select_modes(children):
    all_pids = resolve_processes(_match(TOKEN), select={"mode": "all"})
    assert len(all_pids) == 3
    assert resolve_processes(_match(TOKEN),
                             select={"mode": "first"}) == all_pids[:1]
    assert resolve_processes(_match(TOKEN),
                             select={"mode": "count",
                                     "value": 2}) == all_pids[:2]
    r1 = resolve_processes(_match(TOKEN),
                           select={"mode": "random", "value": 2, "seed": 7})
    r2 = resolve_processes(_match(TOKEN),
                           select={"mode": "random", "value": 2, "seed": 7})
    assert r1 == r2 and len(r1) == 2                    # 同 seed 可复现
    half = resolve_processes(_match(TOKEN),
                             select={"mode": "percent", "value": 50})
    assert len(half) == 2                               # 3 的 50% → 2


def test_unknown_select_mode_and_empty_result(children):
    with pytest.raises(ValueError, match="未知 select.mode"):
        resolve_processes(_match(TOKEN), select={"mode": "chaos"})
    with pytest.raises(ValueError, match="解析为空"):
        resolve_processes({"contains": "no-such-token-xyz"})


def test_exact_mode_matches_name(children):
    pids = resolve_processes({"exact": "python.exe"})
    assert {p.pid for p in children} <= set(pids)       # 平台名 python.exe


def test_system_and_self_excluded(children):
    pids = resolve_processes(_match(TOKEN), exclude=[{"system": True}])
    assert all(p != os.getpid() for p in pids)
