"""一次性回填：pytest 工具域历史 case 记录 dataset 为空，前端任务详情「编辑」
因此报「缺场景信息（旧版本运行）」无法定位脚本行。

根因：旧版 save_tool_case_record 只认 record["dataset"]，而 pytest 适配器后来才往
record 里补 scenario 键，之间落库的 test_case_result/test_case 行 dataset 全 NULL。
回填依据：run.details.work_dir/results/<场景>.jsonl 逐行 external_key → 场景名
（文件名 stem）。只填 NULL、幂等可重跑；work_dir/results 丢失的 run 跳过并提示。

用法（项目根目录执行；库址走 AITE_DB 环境变量，与平台一致）：
  .venv/Scripts/python.exe scripts/backfill_case_dataset.py           # 预览
  .venv/Scripts/python.exe scripts/backfill_case_dataset.py --apply   # 写库
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select

from aite import config
from aite.storage.db import Database
from aite.storage.models import TestCaseResultRow, TestCaseRow, TestRunRow

ROOT = Path(__file__).resolve().parents[1]


def _results_map(work_dir: Path) -> dict[str, str]:
    """results/<场景>.jsonl → {external_key: 场景名}；坏行跳过不中断。"""
    out: dict[str, str] = {}
    for f in sorted((work_dir / "results").glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ek = json.loads(line).get("external_key")
            except json.JSONDecodeError:
                continue
            if ek:
                out[str(ek)] = f.stem
    return out


def _resolve_workdir(raw: str) -> Path | None:
    """work_dir 落库为相对路径（相对当时 CWD），按 CWD→项目根 双候选解析。"""
    p = Path(raw)
    cands = [p] if p.is_absolute() else [Path.cwd() / p, ROOT / p]
    return next((c for c in cands if (c / "results").is_dir()), None)


async def _null_count(session, run_id: str) -> int:
    return int((await session.execute(
        select(func.count()).select_from(TestCaseResultRow).where(
            TestCaseResultRow.run_id == run_id,
            TestCaseResultRow.dataset.is_(None)))).scalar_one())


async def backfill(db: Database, apply: bool = False) -> int:
    """逐 run 扫描 results/*.jsonl 回填；返回可回填/已回填的记录数。"""
    total = 0
    async with db.sessions() as session:
        runs = (await session.execute(
            select(TestRunRow).order_by(TestRunRow.started_at))).scalars().all()
        for run in runs:
            raw = (run.details or {}).get("work_dir")
            workdir = _resolve_workdir(raw) if raw else None
            rows = (await session.execute(
                select(TestCaseResultRow).where(
                    TestCaseResultRow.run_id == run.id,
                    TestCaseResultRow.dataset.is_(None)))).scalars().all()
            if not rows:
                continue
            if workdir is None:
                print(f"  跳过 run {run.run_name}: {await _null_count(session, run.id)} 条"
                      f" NULL，work_dir/results 缺失无法反查")
                continue
            key2scen = _results_map(workdir)
            fill = {r.external_key: key2scen[r.external_key] for r in rows
                    if r.external_key in key2scen}
            missed = len(rows) - len(fill)
            if apply and fill:
                for r in rows:
                    if r.external_key in fill:
                        r.dataset = fill[r.external_key]
                cases = (await session.execute(
                    select(TestCaseRow).where(
                        TestCaseRow.suite_id == run.suite_id,
                        TestCaseRow.external_key.in_(list(fill)),
                        TestCaseRow.dataset.is_(None)))).scalars().all()
                for c in cases:
                    c.dataset = fill[c.external_key]
                await session.commit()
            verb = "已回填" if apply else "可回填"
            print(f"  {verb} run {run.run_name}: {len(fill)} 条"
                  + (f"（{missed} 条不在 results 文件中，保持 NULL）" if missed else ""))
            total += len(fill)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="回填历史 case 记录的 dataset（场景名）")
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省只预览）")
    args = ap.parse_args()
    db = Database(config.db_url_from_env())
    total = asyncio.run(backfill(db, apply=args.apply))
    tail = "" if args.apply else "（未写库，加 --apply 生效）"
    print(f"合计{'已回填' if args.apply else '可回填'} {total} 条{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
