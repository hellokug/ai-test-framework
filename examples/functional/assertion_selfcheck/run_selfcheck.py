"""断言自检一键校验：起假模型 → pytest 引擎跑本目录 cases.jsonl → 逐例核对结果。

数据集约定：cases.jsonl 里 ``bad_*`` 用例（带 ``want`` 字段）设计为失败，
且失败信息须包含 ``want`` 关键字；其余用例设计为通过。本脚本负责：

1. 起假模型（fake_model.py，临时端口）；
2. 以与 pytest_adapter 相同的 argv 形态（``-p aite.functional.harness``）跑真实引擎；
3. 解析 junit-xml，核对每例的「设计期望 vs 实际结果」——全部吻合 = 断言层无回归。

用法（项目根目录）：``.venv/Scripts/python.exe examples/functional/assertion_selfcheck/run_selfcheck.py``
退出码：0 = 每例结果与设计一致；1 = 有偏差（数据集或断言实现需要看）。
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[3]
JUNIT_NAME = "selfcheck-junit.xml"
_PYTEST_TIMEOUT = 300


def load_expectations() -> dict[str, tuple[bool, str]]:
    """cases.jsonl → {id: (设计应过?, want 关键字)}；want 存在 = 设计失败例。"""
    out: dict[str, tuple[bool, str]] = {}
    for line in (BASE / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        want = raw.get("want")
        out[str(raw["id"])] = (want is None, str(want or ""))
    return out


def start_fake_model() -> tuple[subprocess.Popen, int]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, str(BASE / "fake_model.py"), "--port", str(port)],
        cwd=str(BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/chat/completions", timeout=1)
            return proc, port          # 200 = 有点不对但服务在
        except urllib.error.HTTPError:
            return proc, port          # GET 405 = 预期，服务已就绪
        except (urllib.error.URLError, ConnectionError, OSError):
            if proc.poll() is not None:
                raise RuntimeError("fake_model 进程提前退出，检查 fake_model.py 语法") from None
            time.sleep(0.2)
    raise RuntimeError(f"fake_model 10 秒内未就绪（端口 {port}）")


def run_pytest(port: int, junit_path: Path) -> subprocess.CompletedProcess:
    env = os.environ | {
        "AITE_FT_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "AITE_FT_MODEL": "FAKE-MODEL-X",
        "AITE_FT_API_KEY": "sk-selfcheck",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(BASE),
         "-p", "aite.functional.harness", "-p", "no:cacheprovider",
         "-o", "norecursedirs=", f"--junitxml={junit_path}", "-q"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=_PYTEST_TIMEOUT)


def parse_junit(junit_path: Path) -> dict[str, tuple[bool, str]]:
    """junit → {用例 id: (是否通过, 失败/错误信息全文)}。"""
    out: dict[str, tuple[bool, str]] = {}
    for tc in ET.parse(junit_path).getroot().iter("testcase"):
        bad = [c for c in tc if c.tag in ("failure", "error")]
        text = "\n".join((c.text or "") for c in bad)
        text += "\n" + "\n".join(c.get("message") or "" for c in bad)
        out[tc.get("name")] = (not bad, text.strip())
    return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    expectations = load_expectations()
    print(f"数据集 {len(expectations)} 条用例（含设计失败例 "
          f"{sum(1 for ok, _ in expectations.values() if not ok)} 条）")

    proc, port = start_fake_model()
    print(f"fake_model 已就绪：127.0.0.1:{port}")
    try:
        with tempfile.TemporaryDirectory(prefix="aite_selfcheck_") as tmp:
            junit_path = Path(tmp) / JUNIT_NAME
            print("跑 pytest 引擎中……")
            completed = run_pytest(port, junit_path)
            if completed.returncode not in (0, 1):
                # 2=收集期格式错误 / 3,4=内部错误：引擎层问题，直接看输出
                print(completed.stdout[-4000:])
                print(completed.stderr[-2000:])
                print(f"pytest 退出码 {completed.returncode}（非 0/1，先修引擎层）")
                return 1
            actual = parse_junit(junit_path)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    rows, mismatches = [], []
    for name, (passed, text) in actual.items():
        base = re.sub(r"\[r\d+\]$", "", name)     # repeat 展开轮次归并到原用例
        exp = expectations.get(base)
        if exp is None:
            mismatches.append(f"junit 里出现数据集之外的用例: {name}")
            continue
        want_pass, want_msg = exp
        if passed != want_pass or (not want_pass and want_msg not in text):
            mismatches.append(
                f"{name}: 期望{'通过' if want_pass else '失败且含 ' + repr(want_msg)}"
                f"，实际{'通过' if passed else '失败'}"
                + ("" if passed else f"，失败信息首行: {(text.splitlines() or [''])[0][:120]}"))
        rows.append((name, want_pass, passed))

    print(f"\n{'用例':<28} {'设计':<4} {'实际':<4}")
    for name, want_pass, passed in sorted(rows):
        mark = "✓" if want_pass == passed else "✗"
        print(f"{mark} {name:<26} {'应过' if want_pass else '应挂':<4} {'过' if passed else '挂':<4}")

    print(f"\n共 {len(rows)} 例，结果与设计一致 {len(rows) - len(mismatches)} 例")
    if mismatches:
        print("偏差明细：")
        for m in mismatches:
            print(f"  - {m}")
        return 1
    print("全部吻合：断言行为与数据集设计一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
