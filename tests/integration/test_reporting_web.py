"""Reporting renderers + web API tests."""

import json
import re

import pytest

from aite.core.types import (
    CaseMetricRecord,
    MetricSummary,
    RunReport,
    utcnow,
)
from aite.reporting.renderers import HtmlRenderer, JsonRenderer, JunitRenderer, RichRenderer


def sample_report():
    return RunReport(
        run_id="01234567-abcd", run_name="unit-run", status="failed",
        pass_strategy="all_of", started_at=utcnow(), finished_at=utcnow(),
        total_cases=2, error_cases=1, total_cost_usd=0.01,
        config={"target": {"type": "openai_compatible", "model": "qwen2.5:3b",
                           "base_url": "http://localhost:11434/v1"},
                "datasets": [{"type": "builtin", "id": "gsm8k"},
                             {"type": "local", "path": "./examples/data/business_qa.jsonl"}]},
        summaries=[
            MetricSummary(metric="exact_match", value=0.5, threshold=0.8,
                          passed=False, count=2),
            MetricSummary(metric="exact_match", value=1.0, threshold=0.8,
                          passed=True, count=1, dataset="gsm8k"),
        ],
        case_metrics=[CaseMetricRecord(case_id="c1", metric="exact_match", value=0.0,
                                       passed=False, raw={"prediction": "x"})],
    )


class TestRenderers:
    def test_json_renderer(self, tmp_path):
        path = JsonRenderer().render(sample_report(), tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["run_name"] == "unit-run"
        assert data["summaries"][0]["metric"] == "exact_match"

    def test_html_renderer_contains_core_sections(self, tmp_path):
        path = HtmlRenderer().render(sample_report(), tmp_path)
        html = path.read_text(encoding="utf-8")
        assert "unit-run" in html and "exact_match" in html and "FAIL" in html
        assert "qwen2.5:3b" in html          # 被测模型必须可见
        assert "builtin/gsm8k" in html and "business_qa.jsonl" in html  # 数据集必须可见

    def test_rich_renderer_shows_target(self, tmp_path):
        path = RichRenderer().render(sample_report(), tmp_path)
        text = path.read_text(encoding="utf-8")
        assert "model: qwen2.5:3b" in text
        assert "http://localhost:11434/v1" in text
        assert "builtin/gsm8k" in text and "business_qa.jsonl" in text

    def test_report_relpath_model_dataset_time(self):
        from aite.reporting.renderers import report_relpath

        p = report_relpath(sample_report())
        module, model, datasets, leaf = p.parts
        assert module == "misc"                          # 第一层模块（suite.module，缺省 misc）
        assert model == "qwen2.5-3b"                     # 第二层模型，冒号净化
        assert datasets == "gsm8k+business_qa.jsonl"     # 第三层数据集，+ 连接
        assert re.match(r"^\d{8}-\d{6}_01234567$", leaf)  # 第四层 时间_短id

    def test_junit_renderer_xml(self, tmp_path):
        import xml.etree.ElementTree as ET

        path = JunitRenderer().render(sample_report(), tmp_path)
        tree = ET.parse(path)
        suites = tree.getroot()
        assert suites.attrib["failures"] == "0" or int(suites.attrib["failures"]) >= 0
        # overall bucket failed -> at least one failure node? overall failed=1
        cases = suites.findall(".//testcase")
        assert len(cases) == 2


class TestWebApi:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path/'web.db'}")
        with TestClient(app) as client:
            yield client

    def test_health(self, client):
        assert client.get("/api/v1/health").json() == {"status": "ok"}

    def test_suites_crud(self, client):
        ok = client.post("/api/v1/suites", json={
            "name": "web-suite",
            "config_yaml": """
run_name: web-suite
target: {type: openai_compatible, base_url: "http://x/v1", model: m}
datasets: [{type: local, path: a.jsonl}]
metrics: [{exact_match: {threshold: 0.5}}]
"""}).json()
        assert ok["validated"] is True
        items = client.get("/api/v1/suites").json()["items"]
        assert items[0]["name"] == "web-suite"

    def test_invalid_suite_rejected(self, client):
        resp = client.post("/api/v1/suites", json={"name": "bad",
                                                   "config_yaml": "run_name: x\n"})
        assert resp.status_code == 422

    def test_models_crud(self, client, tmp_path, monkeypatch):
        from aite import config
        monkeypatch.setattr(config, "SECRET_STORE_FILE", tmp_path / "secrets.json")
        monkeypatch.setattr(config, "SECRET_KEY_FILE", tmp_path / "secret.key")
        mid = client.post("/api/v1/models", json={
            "name": "gpt4o-mini", "model": "gpt-4o-mini"}).json()["id"]
        assert any(m["id"] == mid for m in client.get("/api/v1/models").json()["items"])
        # 注册回告密钥可解析性（环境变量 > 密钥库）：未归档 False → 归档后 True
        mid2 = client.post("/api/v1/models", json={
            "name": "no-key-model", "model": "m",
            "api_key_env": "TEST_UNARCHIVED_KEY"}).json()
        assert mid2["key_archived"] is False
        client.put("/api/v1/secrets/TEST_UNARCHIVED_KEY", json={"value": "sk-test-123"})
        mid3 = client.post("/api/v1/models", json={
            "name": "archived-key-model", "model": "m",
            "api_key_env": "TEST_UNARCHIVED_KEY"}).json()
        assert mid3["key_archived"] is True
        assert client.delete(f"/api/v1/models/{mid}").json()["deleted"] is True
        for extra in (mid2["id"], mid3["id"]):
            client.delete(f"/api/v1/models/{extra}")
        client.delete("/api/v1/secrets/TEST_UNARCHIVED_KEY")

    def test_run_launch_with_mock(self, client, tmp_path, monkeypatch):
        qa = tmp_path / "qa.jsonl"
        qa.write_text('{"id": "q1", "question": "2+2?", "answer": "4"}\n', encoding="utf-8")
        from aite.core import engine as engine_mod
        from tests.fixtures.mock_components import MockConnector

        monkeypatch.setattr(engine_mod, "resolve_connector",
                            lambda name: lambda config: MockConnector())
        launched = client.post("/api/v1/runs", json={
            "config": {
                "run_name": "web-run",
                "target": {"type": "openai_compatible",
                           "base_url": "http://mock/v1", "model": "m"},
                "datasets": [{"type": "local", "path": str(qa)}],
                "metrics": [{"exact_match": {"threshold": 0.5}}],
                "report": [], "output_dir": str(tmp_path / "rep"),
                "storage": {"url": "sqlite+aiosqlite:///:memory:"},
            }}).json()
        assert launched["status"] == "started"
        # the launched run is async; poll tasks until done
        import time

        for _ in range(100):
            task = client.get(f"/api/v1/tasks/{launched['task_id']}").json()
            if task["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert task["status"] == "done", task
        run = client.get(f"/api/v1/runs/{task['run_id']}").json()
        assert run["status"] == "passed"
        assert any(s["metric"] == "exact_match" for s in run["summaries"])

    def test_manual_eval_flow(self, client, tmp_path, monkeypatch):
        qa = tmp_path / "qa.jsonl"
        qa.write_text('{"id": "q1", "question": "Q", "answer": "A"}\n', encoding="utf-8")
        from aite.core import engine as engine_mod
        from tests.fixtures.mock_components import MockConnector

        monkeypatch.setattr(engine_mod, "resolve_connector",
                            lambda name: lambda config: MockConnector())
        launched = client.post("/api/v1/runs", json={
            "config": {
                "run_name": "manual-run",
                "target": {"type": "openai_compatible",
                           "base_url": "http://mock/v1", "model": "m"},
                "datasets": [{"type": "local", "path": str(qa)}],
                "metrics": ["exact_match"], "report": [],
                "output_dir": str(tmp_path), "storage": {"url": "sqlite+aiosqlite:///:memory:"},
            }}).json()
        import time

        for _ in range(100):
            task = client.get(f"/api/v1/tasks/{launched['task_id']}").json()
            if task["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        created = client.post("/api/v1/manual-eval/tasks", json={
            "run_id": task["run_id"]}).json()
        assert created["created"] == 1
        task_id = created["task_ids"][0]
        client.post(f"/api/v1/manual-eval/tasks/{task_id}/score",
                    json={"score": 9, "reviewer": "bob"})
        tasks = client.get("/api/v1/manual-eval/tasks").json()["items"]
        assert tasks[0]["status"] == "done"
        exported = client.get("/api/v1/manual-eval/export").json()["items"]
        assert exported[0]["score"] == 9

    def test_trends_empty(self, client):
        assert client.get("/api/v1/trends").json()["count"] == 0

    def test_static_index_served(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"aite" in resp.content

    def test_tools_engines(self, client):
        data = client.get("/api/v1/tools/engines").json()
        names = {e["name"] for e in data["items"]}
        assert {"evalscope", "evalscope-perf", "pytest", "locust"} <= names

    def test_tools_benchmarks_catalog(self, client):
        data = client.get("/api/v1/tools/benchmarks",
                          params={"engine": "evalscope-perf"}).json()
        assert data["engine"] == "evalscope-perf"
        assert data["items"][0]["name"] == "chat"
        assert "ttft_ms" in data["items"][0]["metrics"]

    def test_tools_benchmarks_unknown_engine_404(self, client):
        resp = client.get("/api/v1/tools/benchmarks", params={"engine": "nope"})
        assert resp.status_code == 404

    def test_tools_benchmarks_requires_engine(self, client):
        assert client.get("/api/v1/tools/benchmarks").status_code == 422


class TestToolSuiteApi:
    """工具域 API：POST /suites|/runs 的 engine 路由 + timeseries 端点 + DELETE。"""

    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path/'web.db'}")
        with TestClient(app) as client:
            yield client

    @pytest.fixture()
    def fake_tool(self, tmp_path):
        """注册一个假压测引擎：run 时写 timeseries.json 到 work_dir。"""

        from aite.tools.base import ToolAdapter, ToolMetricRow, ToolRunResult, register_tool, unregister_tool

        @register_tool("fake-perf")
        class _FakePerf(ToolAdapter):
            name = "fake-perf"

            def build_config(self, req):
                return {}

            def list_tasks(self):
                return [{"name": "chat", "pretty_name": "fake", "metrics": ["qps"],
                         "tags": ["perf"], "few_shot_num": None}]

            async def run(self, req):
                import json as _json

                req.work_dir.mkdir(parents=True, exist_ok=True)
                ts = {"engine": "fake-perf", "bucket_seconds": 5.0,
                      "series": [{"users": 4, "run": 0, "label": "fake:c4",
                                  "points": [{"t": 0, "ttft_avg": 100.0,
                                              "ttft_p95": 200.0,
                                              "latency_avg": 900.0,
                                              "latency_p95": 1200.0,
                                              "rps": 2.0, "tok_s": 50.0,
                                              "errors": 0}]}],
                      "errors": []}
                (req.work_dir / "timeseries.json").write_text(
                    _json.dumps(ts), encoding="utf-8")
                return ToolRunResult(rows=[ToolMetricRow(
                    dataset="fake:c4", metric="qps", value=2.0, count=10)])

        yield _FakePerf
        unregister_tool("fake-perf")   # 反注册避免污染其他测试

    def _perf_config(self):
        return {
            "run_name": "web-perf",
            "engine": "fake-perf",
            "target": {"type": "openai_compatible", "base_url": "http://x/v1",
                       "model": "m"},
            "perf": {"prompt": "p", "parallel": [4], "duration": 10},
            "output_dir": "./reports",
        }

    def wait_done(self, client, task_id):
        import time
        for _ in range(100):
            task = client.get(f"/api/v1/tasks/{task_id}").json()
            if task.get("status") in ("done", "error"):
                return task
            time.sleep(0.05)
        raise AssertionError("task not finished")

    def test_store_and_run_tool_suite(self, client, fake_tool, tmp_path):
        cfg = self._perf_config()
        cfg["output_dir"] = str(tmp_path / "rep")
        # 1) suite 存储（engine 路由走 parse_tool_suite 校验）
        ok = client.post("/api/v1/suites", json={
            "name": "web-perf", "config": cfg})
        assert ok.status_code == 200 and ok.json()["validated"] is True
        # 2) 按 suite 名运行
        launched = client.post("/api/v1/runs", json={"suite_name": "web-perf"})
        assert launched.status_code == 200
        task = self.wait_done(client, launched.json()["task_id"])
        assert task["status"] == "done"
        run_id = task["run_id"]
        # 3) run 详情带 details（reports/work_dir 落库）
        run = client.get(f"/api/v1/runs/{run_id}").json()
        assert run["details"]["work_dir"]
        assert run["summaries"][0]["metric"] == "qps"
        # 4) timeseries 端点
        ts = client.get(f"/api/v1/runs/{run_id}/timeseries")
        assert ts.status_code == 200
        body = ts.json()
        assert body["engine"] == "fake-perf"
        assert body["series"][0]["points"][0]["ttft_avg"] == 100.0

    def test_run_tool_suite_inline_config(self, client, fake_tool, tmp_path):
        cfg = self._perf_config()
        cfg["output_dir"] = str(tmp_path / "rep2")
        resp = client.post("/api/v1/runs", json={"config": cfg})
        assert resp.status_code == 200
        task = self.wait_done(client, resp.json()["task_id"])
        assert task["status"] == "done"

    def test_invalid_tool_suite_rejected(self, client):
        resp = client.post("/api/v1/runs", json={"config": {
            "run_name": "bad", "engine": "fake-perf"}})   # 缺 target.model 等
        assert resp.status_code == 422

    def test_timeseries_404_for_plain_run(self, client, tmp_path):
        run_id = "00000000-0000-0000-0000-000000000000"
        assert client.get(f"/api/v1/runs/{run_id}/timeseries").status_code == 404

    def test_delete_suite(self, client, fake_tool):
        cfg = self._perf_config()
        assert client.post("/api/v1/suites",
                           json={"name": "del-me", "config": cfg}).status_code == 200
        assert client.delete("/api/v1/suites/del-me").json()["deleted"] == "del-me"
        assert client.get("/api/v1/suites/del-me").status_code == 404
        assert client.delete("/api/v1/suites/del-me").status_code == 404

    def test_stale_bg_tasks_marked_on_startup(self, tmp_path):
        """崩溃残留的 running/pending 任务在 startup 时标记 error（状态机闭合）。"""
        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        storage = f"sqlite+aiosqlite:///{tmp_path/'stale.db'}"
        # 先建库塞入残留中间态任务
        import asyncio

        from aite.storage.db import Database
        from aite.storage.repositories import SqlAlchemyRepository

        async def seed():
            repo = SqlAlchemyRepository(Database(storage))
            await repo.init()
            tid = await repo.create_bg_task("run_suite", {"run_name": "ghost"})
            await repo.update_bg_task(tid, status="running")
            await repo.close()
            return tid

        ghost_id = asyncio.run(seed())
        app = create_app(storage_url=storage)
        with TestClient(app):        # lifespan startup 执行 stale 标记
            pass
        import asyncio as _aio

        async def check():
            repo = SqlAlchemyRepository(Database(storage))
            await repo.init()
            task = await repo.get_bg_task(ghost_id)
            await repo.close()
            return task

        task = _aio.run(check())
        assert task["status"] == "error"
        assert "restarted" in task["error"]

    def test_stream_by_run_id_resolves_task_channel(self, client, fake_tool, tmp_path):
        """run_id 反查 bg_task 订阅正确频道：已完成 run 回放 done 帧后关流。"""
        import json as _json

        cfg = self._perf_config()
        cfg["output_dir"] = str(tmp_path / "rep3")
        launched = client.post("/api/v1/runs", json={"config": cfg})
        task = self.wait_done(client, launched.json()["task_id"])
        run_id = task["run_id"]
        with client.stream("GET", f"/api/v1/runs/{run_id}/stream") as resp:
            assert resp.status_code == 200
            frames = [line for line in resp.iter_lines() if line.startswith("data: ")]
        assert frames, "SSE 应至少回放一帧"
        event = _json.loads(frames[0][6:])
        assert event["type"] in ("done", "error")

    def test_stream_historical_run_without_task(self, tmp_path):
        """CLI 发起的 run 无 bg_task：回一条存态终态帧关流（不挂起、不 404）。"""
        import asyncio

        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        storage = f"sqlite+aiosqlite:///{tmp_path/'hist.db'}"

        async def seed():
            from aite.storage.db import Database
            from aite.storage.repositories import SqlAlchemyRepository

            repo = SqlAlchemyRepository(Database(storage))
            await repo.init()
            run_id = await repo.create_run("cli-run", {})
            await repo.update_run(run_id, status="passed")
            await repo.close()
            return run_id

        run_id = asyncio.run(seed())
        app = create_app(storage_url=storage)
        with TestClient(app) as client:
            with client.stream("GET", f"/api/v1/runs/{run_id}/stream") as resp:
                assert resp.status_code == 200
                frames = [ln for ln in resp.iter_lines() if ln.startswith("data: ")]
        assert len(frames) == 1
        import json as _json

        event = _json.loads(frames[0][6:])
        assert event == {"type": "done", "run_id": run_id, "status": "passed"}

    def test_stream_unknown_run_404(self, client):
        assert client.get("/api/v1/runs/nonexistent/stream").status_code == 404
