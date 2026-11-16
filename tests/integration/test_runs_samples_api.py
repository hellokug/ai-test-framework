"""/runs/{id}/samples 逐样本明细端点集成测试（精度诊断数据源）。

工具域跑分 run 的 test_case_result 恒为空（引擎不逐例驱动），逐样本真值
只存在于 work_dir reviews/*.jsonl；端点负责把它变成可分页/可过滤的 REST。
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from aite.storage.db import Database
from aite.storage.repositories import SqlAlchemyRepository


def _review_row(index, score, prediction="B", target="A"):
    return {
        "index": index, "target": target,
        "messages": [{"role": "user", "content": f"问题{index}：以下哪项关于电磁场正确？"}],
        "sample_score": {
            "sample_id": index,
            "score": {"value": {"accuracy": score}, "status": "success",
                      "extracted_prediction": prediction,
                      "prediction": "模型原始输出文本", "judge_summary": None,
                      "explanation": None, "main_score_name": "accuracy"},
            "sample_metadata": {"correct_answer": "与电场环流相关的选项"},
        },
    }


def _error_row(index):
    """执行失败样本（模型调用异常）：score 无 value 键 → passed=None。"""
    return {
        "index": index, "target": "A",
        "messages": [{"role": "user", "content": f"问题{index}：模型调用失败"}],
        "sample_score": {
            "sample_id": index,
            "score": {"status": "error", "message": "模型调用超时"},
            "sample_metadata": {},
        },
    }


def _write_reviews(work_dir, rows, model="deepseek-chat", ds="gpqa_diamond"):
    d = work_dir / "reviews" / model
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ds}_default.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")


@pytest.fixture()
def seeded(tmp_path):
    """同一 sqlite：独立 repo 实例种 run 行（自含事件循环，写完即关），再起 HTTP。"""
    work_dir = tmp_path / "work"
    _write_reviews(work_dir, [_review_row(0, 1.0, prediction="A"),
                              _review_row(1, 0.0),
                              _review_row(2, 1.0),
                              _error_row(3)])

    async def _seed():
        repo = SqlAlchemyRepository(Database(f"sqlite+aiosqlite:///{tmp_path / 'web.db'}"))
        await repo.init()
        run_id = await repo.create_run("acc-demo", {"engine": "evalscope"})
        await repo.update_run(run_id, status="passed", total_cases=3, error_cases=0,
                              details={"work_dir": str(work_dir),
                                       "reports": {}, "failures": []})
        bare_id = await repo.create_run("no-samples", {"engine": "evalscope"})
        await repo.close()
        return run_id, bare_id

    run_id, bare_id = asyncio.run(_seed())

    from aite.web.app import create_app

    app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
    with TestClient(app) as c:
        yield c, run_id, bare_id


class TestRunSamplesApi:
    def test_samples_shape_and_scoring(self, seeded):
        client, run_id, _ = seeded
        resp = client.get(f"/api/v1/runs/{run_id}/samples")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4 and data["wrong"] == 1
        assert data["passed"] == 2 and data["error"] == 1
        assert data["datasets"] == ["gpqa_diamond"]
        rows = data["items"]
        assert [r["passed"] for r in rows] == [True, False, True, None]
        assert rows[1]["extracted_prediction"] == "B"
        assert rows[1]["correct_answer"] == "与电场环流相关的选项"
        assert rows[0]["question"].startswith("问题0")
        assert rows[0]["score"] == 1.0
        assert rows[3]["status"] == "error" and rows[3]["score"] is None

    def test_wrong_only_filter(self, seeded):
        client, run_id, _ = seeded
        data = client.get(f"/api/v1/runs/{run_id}/samples?wrong_only=true").json()
        assert data["total"] == 1 and data["wrong"] == 1
        assert data["items"][0]["index"] == 1

    def test_pagination(self, seeded):
        client, run_id, _ = seeded
        data = client.get(f"/api/v1/runs/{run_id}/samples?limit=1&offset=1").json()
        assert data["total"] == 4 and len(data["items"]) == 1
        assert data["items"][0]["index"] == 1

    def test_limit_param_validated(self, seeded):
        client, run_id, _ = seeded
        assert client.get(
            f"/api/v1/runs/{run_id}/samples?limit=9999").status_code == 422

    def test_run_without_samples_404(self, seeded):
        client, _, bare_id = seeded
        assert client.get(f"/api/v1/runs/{bare_id}/samples").status_code == 404
        assert client.get("/api/v1/runs/nope/samples").status_code == 404

    def test_corrupted_line_skipped(self, tmp_path):
        work_dir = tmp_path / "w2"
        d = work_dir / "reviews" / "m"
        d.mkdir(parents=True)
        (d / "ds_default.jsonl").write_text(
            "{bad json line}\n" + json.dumps(_review_row(0, 1.0)) + "\n",
            encoding="utf-8")

        async def _seed():
            repo = SqlAlchemyRepository(Database(f"sqlite+aiosqlite:///{tmp_path / 'w2.db'}"))
            await repo.init()
            run_id = await repo.create_run("t", {"engine": "evalscope"})
            await repo.update_run(run_id, details={"work_dir": str(work_dir)})
            await repo.close()
            return run_id

        run_id = asyncio.run(_seed())
        from aite.web.app import create_app

        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'w2.db'}")
        with TestClient(app) as client:
            data = client.get(f"/api/v1/runs/{run_id}/samples").json()
            assert data["total"] == 1
