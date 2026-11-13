"""IAB 隔离实例一条龙（销 2026-11-10 效率账：手工起后端/反代/灌数 6 步）。

用法：
  python scripts/iab_sandbox.py up [--seed <脚本.py>] [--page functional2.html]
      起临时后端（8310，sqlite 临时库）+ 静态稿反代（8776），后端就绪后跑灌数
      脚本，打印页面 URL；PID 与库路径记入 .tmp/iab-sandbox.json。
  python scripts/iab_sandbox.py down
      按 PID 文件停两个子进程（taskkill /T /F）并删临时库。

灌数脚本约定：自己用 httpx 打 http://127.0.0.1:8310/api/v1（不要 curl，中文 JSON
在 Git Bash 下编码损坏）；只写临时库，不碰 data/ 托管根。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / ".tmp"
STATE = TMP / "iab-sandbox.json"
BACKEND_PORT = 8310
PROXY_PORT = 8776
DB = TMP / "iab-sandbox.db"


def _py() -> str:
    return str(ROOT / ".venv" / "Scripts" / "python.exe")


def up(seed: str | None, page: str) -> int:
    if STATE.exists():
        print("已有沙箱在跑（.tmp/iab-sandbox.json 存在），先 down 再 up", file=sys.stderr)
        return 2
    TMP.mkdir(exist_ok=True)
    for old in (DB, DB.with_suffix(".db-shm"), DB.with_suffix(".db-wal")):
        old.unlink(missing_ok=True)
    # 数据集根也隔离到 .tmp（AITE_DATASETS_DIR 优先级最高）：upload/导入落临时目录，
    # 否则会写进 owner 的 data/datasets/；HF 缓存同理（2026-11-13 数据集页验证补）
    ds_root = TMP / "iab-ds-root"
    ds_root.mkdir(exist_ok=True)
    backend = subprocess.Popen([
        _py(), "-m", "aite.cli.main", "serve", "--port", str(BACKEND_PORT),
        "--db", f"sqlite+aiosqlite:///{DB.as_posix()}"], cwd=ROOT,
        env={**__import__("os").environ,
             "AITE_DATASETS_DIR": str(ds_root),
             "HF_HOME": str(TMP / "iab-hf-home")})
    proxy = subprocess.Popen(
        [_py(), str(ROOT / "web-ui" / "mockups" / "serve.py"), str(PROXY_PORT)],
        cwd=ROOT, env={**__import__("os").environ,
                       "AITE_API": f"http://127.0.0.1:{BACKEND_PORT}"})
    STATE.write_text(json.dumps(
        {"backend": backend.pid, "proxy": proxy.pid, "db": str(DB)}, indent=1))
    import httpx
    for _ in range(30):
        try:
            httpx.get(f"http://127.0.0.1:{BACKEND_PORT}/api/v1/common/models", timeout=2)
            break
        except Exception:
            if backend.poll() is not None:
                down()
                print("后端启动即退，已清理；日志见后台输出", file=sys.stderr)
                return 1
            time.sleep(1)
    else:
        down()
        print("后端 30s 未就绪，已清理", file=sys.stderr)
        return 1
    if seed:
        rc = subprocess.call([_py(), str(Path(seed).resolve())], cwd=ROOT)
        if rc != 0:
            print(f"灌数脚本退出码 {rc}（实例保持运行，可自行排查）", file=sys.stderr)
    print(f"页面 http://127.0.0.1:{PROXY_PORT}/web-ui/mockups/v1/{page}")
    print("结束验证后：python scripts/iab_sandbox.py down")
    return 0


def down() -> int:
    if not STATE.exists():
        print("无运行中的沙箱")
        return 0
    st = json.loads(STATE.read_text())
    for key in ("proxy", "backend"):          # 先代理后后端，避免反代还在转发
        pid = st.get(key)
        if pid:
            subprocess.call(["taskkill", "/T", "/F", "/PID", str(pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    db = Path(st.get("db", ""))
    for f in (db, db.with_suffix(".db-shm"), db.with_suffix(".db-wal")):
        f.unlink(missing_ok=True)
    import shutil
    for d in (TMP / "iab-ds-root", TMP / "iab-hf-home"):
        shutil.rmtree(d, ignore_errors=True)
    STATE.unlink(missing_ok=True)
    print("沙箱已停干净（子进程 + 临时库）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    up_p = sub.add_parser("up", help="起隔离后端 + 反代 + 可选灌数")
    up_p.add_argument("--seed", help="就绪后执行的灌数脚本（httpx 打 8310）")
    up_p.add_argument("--page", default="functional2.html", help="打印的页面文件名")
    sub.add_parser("down", help="停子进程并删临时库")
    args = ap.parse_args()
    return up(args.seed, args.page) if args.cmd == "up" else down()


if __name__ == "__main__":
    sys.exit(main())
