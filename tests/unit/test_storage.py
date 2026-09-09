"""Storage layer tests: schema, repository CRUD, trends."""

import pytest

from aite.core.types import TargetResponse, TestCase, Timings


@pytest.mark.asyncio
async def test_schema_created(repo):
    import sqlalchemy as sa

    tables = set()
    async with repo.db.engine.connect() as conn:
        rows = await conn.execute(sa.text(
            "SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {r[0] for r in rows}
    for expected in ("model_config", "test_suite", "test_case", "test_run",
                     "test_case_result", "metric_result", "run_metric_summary",
                     "manual_eval_task", "manual_eval_score", "bg_task"):
        assert expected in tables, expected


@pytest.mark.asyncio
async def test_run_lifecycle(repo):
    suite_id = await repo.upsert_suite("s1", {"a": 1})
    run_id = await repo.create_run("s1", {"target": {}}, suite_id)
    await repo.update_run(run_id, status="passed", total_cases=3, cost_usd=0.01)
    run = await repo.get_run(run_id)
    assert run["status"] == "passed" and run["total_cases"] == 3
    runs = await repo.list_runs()
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_case_roundtrip(repo):
    suite_id = await repo.upsert_suite("s2")
    row_id = await repo.save_test_case(suite_id, TestCase(
        id="case-9", input="q", expected="a", metadata={"topic": "x"}))
    # upsert same key -> same row
    row_id2 = await repo.save_test_case(suite_id, TestCase(
        id="case-9", input="q2", expected="a2"))
    assert row_id2 == row_id
    cases = await repo.list_test_cases(suite_id)
    assert len(cases) == 1 and cases[0]["input"] == "q2"
    # variant keys differ
    row3 = await repo.save_test_case(suite_id, TestCase(
        id="case-9", input="q", variant="repeat-1", variant_of="case-9"))
    assert row3 != row_id


@pytest.mark.asyncio
async def test_results_and_summaries(repo):
    suite_id = await repo.upsert_suite("s3")
    run_id = await repo.create_run("s3", {})
    case_row = await repo.save_test_case(suite_id, TestCase(id="c1", input="q"))
    result_row = await repo.save_case_result(
        run_id, case_row, "c1", TargetResponse(output="ans", timings=Timings(total_ms=10)))
    from aite.core.types import MetricResult

    await repo.save_metric_results(run_id, result_row, [
        MetricResult(metric="exact_match", value=1.0, passed=True),
    ])
    metrics = await repo.list_metric_results(run_id)
    assert metrics[0]["metric"] == "exact_match" and metrics[0]["value"] == 1.0

    from aite.core.types import MetricSummary

    await repo.save_metric_summary(run_id, MetricSummary(
        metric="exact_match", value=0.9, threshold=0.8, passed=True, count=1))
    summaries = await repo.list_metric_summaries(run_id)
    assert summaries[0]["metric"] == "exact_match"
    trends = await repo.trends(run_name="s3", metric="exact_match", days=1)
    assert len(trends) == 1


@pytest.mark.asyncio
async def test_manual_eval_flow(repo):
    task_id = await repo.create_manual_task({"title": "t", "case_input": "q"})
    await repo.submit_manual_score(task_id, 8.5, reviewer="alice", passed=True)
    tasks = await repo.list_manual_tasks()
    assert tasks[0]["status"] == "done" and tasks[0]["scores"][0]["score"] == 8.5
    exported = await repo.export_manual_scores()
    assert exported[0]["reviewer"] == "alice"


@pytest.mark.asyncio
async def test_model_configs(repo):
    mid = await repo.save_model_config({"name": "gpt4o", "model": "gpt-4o",
                                        "base_url": "https://api.openai.com/v1"})
    items = await repo.list_model_configs()
    assert items[0]["id"] == mid
    assert await repo.delete_model_config(mid) is True
    assert await repo.list_model_configs() == []


@pytest.mark.asyncio
async def test_bg_task(repo):
    tid = await repo.create_bg_task("run_suite", {"run_name": "x"})
    await repo.update_bg_task(tid, status="done", run_id="abc")
    task = await repo.get_bg_task(tid)
    assert task["status"] == "done"
    assert task["finished_at"] is not None


@pytest.mark.asyncio
async def test_legacy_db_details_column_migration(tmp_path):
    """老库（无 details 列）init 时轻迁移补列，免重建。"""
    import sqlalchemy as sa

    from aite.storage.db import Database
    from aite.storage.repositories import SqlAlchemyRepository

    db_file = tmp_path / "legacy.db"
    engine = sa.create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE test_run (id CHAR(36) PRIMARY KEY, run_name VARCHAR(512),"
            " suite_id CHAR(36), status VARCHAR(32), overall_score FLOAT,"
            " config_snapshot JSON, total_cases INT, error_cases INT,"
            " cost_usd FLOAT, started_at DATETIME, finished_at DATETIME,"
            " error TEXT)"))
        conn.execute(sa.text(
            "INSERT INTO test_run (id, run_name, status, config_snapshot,"
            " total_cases, error_cases, cost_usd, started_at)"
            " VALUES ('r1', 'legacy', 'passed', '{}', 1, 0, 0,"
            " CURRENT_TIMESTAMP)"))
    engine.dispose()

    db = Database(f"sqlite+aiosqlite:///{db_file}")
    repo = SqlAlchemyRepository(db)
    await repo.init()
    try:
        run = await repo.get_run("r1")
        assert run["details"] == {}            # 补列成功且默认空
        await repo.update_run("r1", details={"work_dir": "x"})
        assert (await repo.get_run("r1"))["details"]["work_dir"] == "x"
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_mark_stale_bg_tasks(repo):
    """状态机闭合：pending/running 残留任务启动时批量标记 error，终态不动。"""
    done_id = await repo.create_bg_task("run_suite", {"run_name": "a"})
    await repo.update_bg_task(done_id, status="done")
    running_id = await repo.create_bg_task("run_suite", {"run_name": "b"})
    await repo.update_bg_task(running_id, status="running")
    pending_id = await repo.create_bg_task("run_suite", {"run_name": "c"})

    marked = await repo.mark_stale_bg_tasks()

    assert marked == 2
    assert (await repo.get_bg_task(done_id))["status"] == "done"       # 终态不动
    assert (await repo.get_bg_task(running_id))["status"] == "error"
    assert (await repo.get_bg_task(pending_id))["status"] == "error"
