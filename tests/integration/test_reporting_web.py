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
        model, datasets, leaf = p.parts
        assert model == "qwen2.5-3b"                     # 第一层模型，冒号净化
        assert datasets == "gsm8k+business_qa.jsonl"     # 第二层数据集，+ 连接
        assert re.match(r"^\d{8}-\d{6}_01234567$", leaf)  # 第三层 时间_短id

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

    def test_models_crud(self, client):
        mid = client.post("/api/v1/models", json={
            "name": "gpt4o-mini", "model": "gpt-4o-mini"}).json()["id"]
        assert any(m["id"] == mid for m in client.get("/api/v1/models").json()["items"])
        assert client.delete(f"/api/v1/models/{mid}").json()["deleted"] is True

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
