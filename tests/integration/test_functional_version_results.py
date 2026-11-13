"""版本结果归档与大屏聚合测试（V1-PLAN §10.5）。

archive/覆盖语义：asyncio.run + 独立 SQLite 直驱（同 loop 内建 repo）；
compute_board 纯函数判定矩阵；API 层 = 带外种子同库文件 + TestClient 查询
（避免跨 loop 驱动 app 的 aiosqlite 连接）。

真实执行链（任务终态 → archive_maybe 钩子）由 suite_task 薄委托覆盖：钩子
本身的守卫（非 functional / repo 异常不外抛）在本文件桩测。
"""

import asyncio

import pytest

from aite.core.types import MetricSummary, RunReport
from aite.service.version_results import (
    archive_functional_result,
    archive_maybe,
    compute_board,
)


def _report(rates: dict, run_id: str = "run-1", status: str = "passed"):
    summaries = [MetricSummary(metric="pass_rate", dataset=f"pytest:{k}",
                               value=v, count=10) for k, v in rates.items()]
    return RunReport(run_id=run_id, run_name="ft-suite", status=status,
                     total_cases=10 * len(rates), summaries=summaries)


def _spec(version: str | None = "v1.2.0", profile: str = "deepseek") -> dict:
    spec = {"module": "functional", "engine": "pytest",
            "target": {"profile": profile},
            "tools": {"tests_dir": "./scenarios"}}
    if version is not None:
        spec["version"] = version
    return spec


class _ExplodingRepo:
    async def upsert_version_result(self, entry):
        raise RuntimeError("db down")


class TestArchive:
    def test_archive_and_overwrite_semantics(self, tmp_path):
        async def main():
            from aite.storage.db import Database
            from aite.storage.repositories import SqlAlchemyRepository
            repo = SqlAlchemyRepository(
                Database(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}"))
            await repo.init()
            first = await archive_functional_result(
                repo, spec=_spec(), report=_report({"api_params": 0.955},
                                                   run_id="run-1"))
            again = await archive_functional_result(
                repo, spec=_spec(), report=_report({"api_params": 0.90},
                                                   run_id="run-2"))
            rows = await repo.list_version_results(domain="functional")
            await repo.close()
            return first, again, rows

        first, again, rows = asyncio.run(main())
        assert first["details"]["overall"] == 0.955
        assert len(rows) == 1                              # 同键覆盖取最新
        assert rows[0]["run_id"] == "run-2"
        assert again["details"]["dimensions"] == {"api_params": 0.9}

    def test_archive_skip_without_version_or_rows(self, tmp_path):
        async def main():
            from aite.storage.db import Database
            from aite.storage.repositories import SqlAlchemyRepository
            repo = SqlAlchemyRepository(
                Database(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}"))
            await repo.init()
            none_spec = await archive_functional_result(
                repo, spec=_spec(version=None), report=_report({"s": 1.0}))
            empty = await archive_functional_result(
                repo, spec=_spec(),
                report=RunReport(run_id="r", run_name="n", summaries=[]))
            rows = await repo.list_version_results(domain="functional")
            await repo.close()
            return none_spec, empty, rows

        none_spec, empty, rows = asyncio.run(main())
        assert none_spec is None and empty is None and rows == []

    def test_archive_skips_error_status(self, tmp_path):
        async def main():
            from aite.storage.db import Database
            from aite.storage.repositories import SqlAlchemyRepository
            repo = SqlAlchemyRepository(
                Database(f"sqlite+aiosqlite:///{tmp_path / 'e.db'}"))
            await repo.init()
            row = await archive_functional_result(
                repo, spec=_spec(), report=_report({"s": 1.0}, status="error"))
            rows = await repo.list_version_results(domain="functional")
            await repo.close()
            return row, rows

        row, rows = asyncio.run(main())
        assert row is None and rows == []             # 崩溃半截数据不入矩阵

    def test_archive_maybe_guards(self, tmp_path):
        async def main():
            await archive_maybe(_ExplodingRepo(), _spec(), _report({"s": 1.0}))
            await archive_maybe(_ExplodingRepo(),       # 非 functional：不触 repo
                                {"module": "accuracy"}, _report({"s": 1.0}))
            return "no-raise"

        assert asyncio.run(main()) == "no-raise"

    def test_model_extraction(self, tmp_path):
        async def main():
            from aite.storage.db import Database
            from aite.storage.repositories import SqlAlchemyRepository
            repo = SqlAlchemyRepository(
                Database(f"sqlite+aiosqlite:///{tmp_path / 'c.db'}"))
            await repo.init()
            row = await archive_functional_result(
                repo, spec=_spec(profile="qwen2.5-72b"),
                report=_report({"s": 1.0}))
            await repo.close()
            return row

        assert asyncio.run(main())["model"] == "qwen2.5-72b"


class TestBoard:
    def _rows(self):
        def entry(version, dims, overall):
            return {"domain": "functional", "version": version,
                    "model": "deepseek", "run_id": f"run-{version}",
                    "details": {"dimensions": dims, "overall": overall,
                                "cases": 64, "run_id": f"run-{version}"}}

        return [entry("v1.2.0", {"api_params": 0.909, "agent_tasks": 1.0,
                                 "api_stream": 1.0}, 0.938),
                entry("v1.3.0-rc", {"api_params": 0.955, "agent_tasks": 0.917,
                                    "api_shape": 0.8, "new_dim": 1.0}, 0.953)]

    def test_matrix_verdict_and_kpi(self):
        board = compute_board(self._rows(), target="v1.3.0-rc",
                              baseline="v1.2.0")
        by_dim = {d["scenario"]: d for d in board["dimensions"]}
        assert by_dim["api_params"]["verdict"] == "达标"
        assert by_dim["agent_tasks"]["verdict"] == "劣化"
        assert by_dim["agent_tasks"]["delta"] == pytest.approx(-0.083)
        assert by_dim["api_stream"]["verdict"] == "缺失"     # 基线有、目标无
        assert by_dim["new_dim"]["verdict"] == "新增"
        assert board["kpi"]["delta"] == pytest.approx(0.015)
        assert board["kpi"]["degraded"] == 1
        assert board["kpi"]["improved"] == 1          # api_params +0.046
        assert board["kpi"]["added"] == 2             # new_dim + api_shape（基线无）
        assert board["kpi"]["missing"] == 1           # api_stream（目标版缺失）
        assert board["meta"]["target_run"] == "run-v1.3.0-rc"

    def test_missing_version_raises(self):
        with pytest.raises(ValueError, match="无归档结果"):
            compute_board(self._rows(), target="v9", baseline="v1.2.0")


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        import json

        from fastapi.testclient import TestClient

        from aite import config
        from aite.storage.db import Database
        from aite.storage.repositories import SqlAlchemyRepository
        from aite.web.app import create_app

        scen_root = tmp_path / "scen"
        monkeypatch.setattr(config, "DEFAULT_FUNCTIONAL_SCENARIOS_DIR",
                            str(scen_root))
        # 托管根放一个真实场景（manifest 带中文名称）→ 验证大屏维度行中文名 join
        scen = scen_root / "api_params"
        scen.mkdir(parents=True)
        (scen / "manifest.json").write_text(
            json.dumps({"display_name": "参数校验", "dimension": "",
                        "description": ""}, ensure_ascii=False), encoding="utf-8")

        db_url = f"sqlite+aiosqlite:///{tmp_path / 'web.db'}"
        app = create_app(storage_url=db_url)
        with TestClient(app) as c:
            # 带外种子：同库文件独立连接（WAL + busy_timeout 支持并发写）
            async def seed():
                repo = SqlAlchemyRepository(Database(db_url))
                await repo.init()
                await repo.upsert_version_result({
                    "domain": "functional", "version": "v1.2.0",
                    "model": "deepseek", "run_id": "run-b",
                    "details": {"dimensions": {"api_params": 0.909,
                                               "agent_tasks": 1.0},
                                "overall": 0.938, "cases": 64,
                                "run_id": "run-b"}})
                await repo.upsert_version_result({
                    "domain": "functional", "version": "v1.3.0-rc",
                    "model": "deepseek", "run_id": "run-t",
                    "details": {"dimensions": {"api_params": 0.955,
                                               "agent_tasks": 0.917},
                                "overall": 0.953, "cases": 64,
                                "run_id": "run-t"}})
                await repo.close()
            asyncio.run(seed())
            yield c

    def test_version_results_list_and_filter(self, client):
        items = client.get("/api/v1/functional/version-results").json()
        assert items["count"] == 2
        one = client.get("/api/v1/functional/version-results",
                         params={"version": "v1.2.0"}).json()
        assert one["count"] == 1
        assert one["items"][0]["details"]["overall"] == 0.938

    def test_quality_board_endpoint(self, client):
        board = client.get("/api/v1/functional/quality/board", params={
            "model": "deepseek", "target": "v1.3.0-rc",
            "baseline": "v1.2.0"})
        assert board.status_code == 200
        body = board.json()
        assert body["kpi"]["degraded"] == 1
        assert len(body["dimensions"]) == 2
        by_dim = {d["scenario"]: d for d in body["dimensions"]}
        # 中文名称 join：托管根有 manifest 的取显示名，没有的回退场景 id
        assert by_dim["api_params"]["display_name"] == "参数校验"
        assert by_dim["agent_tasks"]["display_name"] == "agent_tasks"

    def test_quality_board_missing_baseline_422(self, client):
        resp = client.get("/api/v1/functional/quality/board", params={
            "model": "deepseek", "target": "v1.3.0-rc", "baseline": "v0.9"})
        assert resp.status_code == 422
        assert "无归档结果" in resp.json()["detail"]
