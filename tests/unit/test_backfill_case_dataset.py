"""回填脚本回归：pytest 工具域历史 case 记录 dataset 为空（旧版落库未写场景），
前端任务详情「编辑」因此报「缺场景信息（旧版本运行）」。脚本从
run.details.work_dir/results/<场景>.jsonl 按 external_key 反查场景名回填，
只填 NULL、幂等；work_dir 丢失的 run 跳过不报错。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from aite.storage.db import Database
from aite.storage.models import TestCaseRow
from aite.storage.repositories import SqlAlchemyRepository

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backfill_case_dataset.py"


@pytest.fixture()
def bf():
    spec = importlib.util.spec_from_file_location("backfill_case_dataset", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _seed_run(repo: SqlAlchemyRepository, name: str, work_dir: str | None,
                    keys: list[str], scen: str = "param_validation") -> str:
    suite_id = await repo.upsert_suite(f"suite-{name}")
    run_id = await repo.create_run(name, {"engine": "pytest"}, suite_id=suite_id)
    for k in keys:
        rec = {"external_key": k, "passed": True}   # 无 scenario/dataset 键 = 旧版落库形态
        await repo.save_tool_case_record(run_id, suite_id, rec)
    details = {"work_dir": work_dir} if work_dir else {}
    await repo.update_run(run_id, details=details)
    return run_id


def _write_results(work_dir: Path, scen: str, keys: list[str]) -> None:
    rdir = work_dir / "results"
    rdir.mkdir(parents=True)
    lines = [json.dumps({"external_key": k, "passed": True}) for k in keys]
    (rdir / f"{scen}.jsonl").write_text("\n".join(lines), encoding="utf-8")


@pytest.mark.asyncio
async def test_backfill_fills_null_dataset_from_results_files(db_url, tmp_path, bf):
    repo = SqlAlchemyRepository(Database(db_url))
    await repo.init()
    wf = tmp_path / "wf"
    _write_results(wf, "param_validation", ["TC_001_1", "TC_001_003"])
    run_id = await _seed_run(repo, "run-ok", str(wf), ["TC_001_1", "TC_001_003"])

    n = await bf.backfill(Database(db_url), apply=True)

    assert n == 2
    items = {i["external_key"]: i["dataset"] for i in await repo.list_case_results(run_id)}
    assert items == {"TC_001_1": "param_validation", "TC_001_003": "param_validation"}
    async with Database(db_url).sessions() as session:
        rows = (await session.execute(
            select(TestCaseRow).where(TestCaseRow.external_key == "TC_001_1"))).scalars().all()
        assert [r.dataset for r in rows] == ["param_validation"]
    await repo.close()


@pytest.mark.asyncio
async def test_backfill_dry_run_and_idempotent(db_url, tmp_path, bf):
    repo = SqlAlchemyRepository(Database(db_url))
    await repo.init()
    wf = tmp_path / "wf"
    _write_results(wf, "s2", ["K1"])
    run_id = await _seed_run(repo, "run-dr", str(wf), ["K1"])

    assert await bf.backfill(Database(db_url), apply=False) == 1   # 预览报可回填数
    items = await repo.list_case_results(run_id)
    assert [i["dataset"] for i in items] == [None]                 # 但不写库

    assert await bf.backfill(Database(db_url), apply=True) == 1
    assert await bf.backfill(Database(db_url), apply=True) == 0   # 幂等：非 NULL 不再动
    assert [i["dataset"] for i in await repo.list_case_results(run_id)] == ["s2"]
    await repo.close()


@pytest.mark.asyncio
async def test_backfill_skips_run_without_work_dir(db_url, bf):
    repo = SqlAlchemyRepository(Database(db_url))
    await repo.init()
    run_id = await _seed_run(repo, "run-lost", None, ["oc1"])
    assert await bf.backfill(Database(db_url), apply=True) == 0
    assert [i["dataset"] for i in await repo.list_case_results(run_id)] == [None]
    await repo.close()
