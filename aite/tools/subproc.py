"""外部工具子进程执行的公共层（pytest / evalscope-perf / locust 三适配器收口）。

统一三件重复事（CLAUDE.md 原则 3）：env 合并（Windows 缺 SystemRoot 会崩）、
PYTHONIOENCODING、TimeoutExpired 与日志落盘。失败文案含调用方上下文
（档位号/超时秒数），故非零/超时**不在此抛错**，返回 outcome 由调用方定语义。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


def which_or_fail(name: str, hint: str) -> str:
    """定位外部 CLI；缺失时抛带安装指引的 RuntimeError。

    PATH 找不到时回退解释器同目录（venv 的 Scripts/bin）——aite CLI 常在
    未激活 venv 的 shell 里以 venv 解释器运行，pip 装的 locust/evalscope
    可执行文件与 python 同目录而非 PATH 上（教训：Windows 实测 which 落空
    致任务误判工具缺失）。
    """
    binary = shutil.which(name)
    if binary is None:
        import sys

        for suffix in ("", ".exe", ".cmd", ".bat"):
            candidate = Path(sys.executable).with_name(name + suffix)
            if candidate.is_file():
                return str(candidate)
        raise RuntimeError(hint)
    return binary


def run_tool_subprocess(args: list[str], *, log_path: Path, timeout: int,
                        env_extra: dict[str, str] | None = None,
                        append_log: bool = False,
                        log_prefix: str = "") -> tuple[int | None, str, str]:
    """执行子进程并落盘日志，返回 (rc, out, err)。

    rc=None 表示超时（日志已写）；其余语义（如 pytest 的 rc=1=有失败用例属
    正常完成）由调用方裁定并组织错误文案。log_prefix 写在日志条目头部
    （多档执行时标注档位）。所有输出 errors=replace 解码。
    """
    env = os.environ | {"PYTHONIOENCODING": "utf-8"} | (env_extra or {})
    try:
        proc = subprocess.run(args, capture_output=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", errors="replace")
        err = (exc.stderr or b"").decode("utf-8", errors="replace")
        _write(log_path, f"{log_prefix}SUBPROCESS TIMEOUT after {timeout}s\n{out}{err}",
               append_log)
        return None, out, err
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    _write(log_path, log_prefix + out + err, append_log)
    return proc.returncode, out, err


def _write(log_path: Path, text: str, append: bool) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if append and log_path.is_file():
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("\n" + text)
    else:
        log_path.write_text(text, encoding="utf-8")


def kill_tree(proc: subprocess.Popen) -> None:
    """终止整个进程树。Windows 上 ``proc.kill()`` 只杀直接子进程——evalscope
    长任务的孙进程（判分/agentic 辅助）会残留占资源；taskkill /T 一并收割，
    taskkill 不可用或失败时回退单杀。"""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    with contextlib.suppress(OSError, ProcessLookupError):
        proc.kill()


def glob_report_files(base: Path, patterns: tuple[str, ...]) -> dict[str, str]:
    """按 patterns 收集工具产出报告文件（文件名 → 绝对路径）。"""
    files: dict[str, str] = {}
    for pattern in patterns:
        for p in sorted(base.glob(pattern)):
            files[p.name] = str(p)
    return files
