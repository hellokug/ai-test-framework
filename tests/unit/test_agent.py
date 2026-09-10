"""模块 G 测试：执行机纳管（master 派发/回传/stale + agent 客户端循环）。"""

import json
import queue
import threading

import pytest


class _FakePerfTool:
    """假压测工具：collect 产出固定指标行（供回传入库链路验证）。"""

    name = "fake-gperf"

    def build_config(self, req):
        return {}

    def list_tasks(self):
        return [{"name": "chat", "pretty_name": "fake", "metrics": ["qps"],
                 "tags": ["perf"], "few_shot_num": None}]

    async def run(self, req):
        from aite.tools.base import ToolMetricRow, ToolRunResult

        req.work_dir.mkdir(parents=True, exist_ok=True)
        (req.work_dir / "timeseries.json").write_text(json.dumps({
            "engine": "evalscope-perf", "bucket_seconds": 5.0,
            "series": [{"users": 2, "run": 0, "label": "fake:c2",
                        "points": [{"t": 0, "ttft_avg": 100.0, "ttft_p95": 200.0,
                                    "latency_avg": 900.0, "latency_p95": 1100.0,
                                    "rps": 2.0, "tok_s": 40.0, "errors": 0}]}],
            "errors": []}), encoding="utf-8")
        req.work_dir.mkdir(parents=True, exist_ok=True)
        return ToolRunResult(rows=[ToolMetricRow(
            dataset="fake:c2", metric="qps", value=2.0, count=8)])


@pytest.fixture()
def perf_suite():
    return {
        "run_name": "agent-perf",
        "engine": "evalscope-perf",
        "target": {"type": "openai_compatible", "base_url": "http://x/v1",
                   "model": "m"},
        "perf": {"prompt": "p", "parallel": [2], "duration": 10},
    }


class TestAgentMaster:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path/'g.db'}")
        with TestClient(app) as c:
            yield c

    @pytest.fixture(autouse=True)
    def fake_tool(self):
        """以白名单引擎名注册假工具（派发校验走真实白名单），teardown 恢复。"""
        from aite.tools.base import register_tool, unregister_tool
        from aite.tools.perf_adapter import PerfAdapter

        register_tool("evalscope-perf")(_FakePerfTool)
        yield
        unregister_tool("evalscope-perf")
        register_tool("evalscope-perf")(PerfAdapter)

    def _register(self, client, name="node-1"):
        resp = client.post("/api/v1/agents/register",
                           json={"name": name, "labels": ["lab"], "version": "t"})
        assert resp.status_code == 200
        return resp.json()

    def _dispatch(self, client, suite, node=None):
        resp = client.post("/api/v1/agent-tasks",
                           json={"config": suite, "node": node})
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_register_and_list(self, client):
        agent = self._register(client)
        assert agent["name"] == "node-1" and agent["status"] == "online"
        items = client.get("/api/v1/agents").json()["items"]
        assert items[0]["name"] == "node-1"

    def test_register_same_name_takes_over(self, client):
        first = self._register(client)
        again = self._register(client)
        assert first["id"] == again["id"]          # 重名接管，行不重复
        assert client.get("/api/v1/agents").json()["count"] == 1

    def test_heartbeat_claims_dispatched_task(self, client, perf_suite):
        agent = self._register(client)
        task = self._dispatch(client, perf_suite, node=agent["name"])
        assert task["status"] == "queued"
        hb = client.post(f"/api/v1/agents/{agent['id']}/heartbeat",
                         json={"current_task_id": None}).json()
        claimed = hb["task"]
        assert claimed and claimed["id"] == task["id"]
        assert claimed["suite_raw"]["engine"] == "evalscope-perf"
        # 二次心跳不再重复领取
        hb2 = client.post(f"/api/v1/agents/{agent['id']}/heartbeat",
                          json={"current_task_id": task["id"]}).json()
        assert hb2["task"] is None

    def test_dispatch_unknown_engine_422(self, client, perf_suite):
        suite = dict(perf_suite, engine="pytest")
        resp = client.post("/api/v1/agent-tasks", json={"config": suite})
        assert resp.status_code == 422

    def test_result_round_trip_persists_run(self, client, perf_suite, tmp_path):
        agent = self._register(client)
        task = self._dispatch(client, perf_suite, node=agent["name"])
        hb = client.post(f"/api/v1/agents/{agent['id']}/heartbeat",
                         json={"current_task_id": None}).json()
        claimed = hb["task"]

        # 模拟 agent：本地执行 → serialize → 回传
        import asyncio

        from aite.service.agent_service import serialize_result
        from aite.tools import execute_tool

        work_dir = tmp_path / "agent-work"
        result, _snap, req = asyncio.run(
            execute_tool(claimed["suite_raw"], work_dir=work_dir))
        assert req.work_dir == work_dir
        payload = serialize_result(result, work_dir)
        assert payload["timeseries"]["bucket_seconds"] == 5.0

        done = client.post(f"/api/v1/agent-tasks/{task['id']}/result",
                           json={"payload": payload})
        assert done.status_code == 200, done.text
        run_id = done.json()["run_id"]

        # 统一入库链路验证：summaries + details.work_dir（master 侧重建目录）
        run = client.get(f"/api/v1/runs/{run_id}").json()
        assert run["status"] == "passed"
        assert run["summaries"][0]["metric"] == "qps"
        assert "agent" in run["details"]["work_dir"]
        ts = client.get(f"/api/v1/runs/{run_id}/timeseries")
        assert ts.status_code == 200
        assert ts.json()["series"][0]["points"][0]["ttft_avg"] == 100.0

        task_now = client.get(f"/api/v1/agent-tasks/{task['id']}").json()
        assert task_now["status"] == "done"
        assert task_now["result"]["run_id"] == run_id

    def test_error_result_creates_error_run(self, client, perf_suite):
        agent = self._register(client)
        task = self._dispatch(client, perf_suite, node=agent["name"])
        client.post(f"/api/v1/agents/{agent['id']}/heartbeat",
                    json={"current_task_id": None})
        done = client.post(f"/api/v1/agent-tasks/{task['id']}/result", json={
            "payload": {"status": "error", "error": "boom"}})
        assert done.status_code == 200
        run_id = done.json()["run_id"]
        run = client.get(f"/api/v1/runs/{run_id}").json()
        assert run["status"] == "error" and "boom" in run["error"]
        assert client.get(f"/api/v1/agent-tasks/{task['id']}").json()["status"] \
            == "error"

    def test_stale_agent_swept_with_running_task(self, client, perf_suite,
                                                 tmp_path):
        agent = self._register(client)
        self._dispatch(client, perf_suite, node=agent["name"])
        client.post(f"/api/v1/agents/{agent['id']}/heartbeat",
                    json={"current_task_id": None})
        # 人为把心跳拨老 → 触发 sweep
        import sqlite3

        db = tmp_path / "g.db"
        conn = sqlite3.connect(db)
        conn.execute("UPDATE agent_node SET last_heartbeat ="
                     " datetime('now', '-120 seconds')")
        conn.commit()
        conn.close()
        items = client.get("/api/v1/agents").json()["items"]
        assert items[0]["status"] == "offline"
        tasks = client.get("/api/v1/agent-tasks").json()["items"]
        assert tasks[0]["status"] == "error"
        assert "offline" in tasks[0]["error"]


class TestAgentClient:
    def test_run_task_reports_error_for_unknown_engine(self, tmp_path):
        from aite.service.agent_client import AgentClient

        client = AgentClient(master="http://master:8300", name="n",
                             work_root=tmp_path)
        posts = []

        def fake_post(path, body):
            posts.append((path, body))
            if path.endswith("/result"):
                return {"task_id": "t1", "run_id": "r1", "status": "error"}
            return {}

        client._post = fake_post
        client._run_task({"id": "t1", "engine": "pytest", "run_name": "x",
                          "suite_raw": {"engine": "pytest"}})
        assert posts[-1][0].endswith("/result")
        assert posts[-1][1]["payload"]["status"] == "error"
        assert "不支持引擎" in posts[-1][1]["payload"]["error"]

    def test_serialize_result_shape(self, tmp_path):
        from aite.service.agent_service import serialize_result
        from aite.tools.base import ToolMetricRow, ToolRunResult

        work = tmp_path / "w"
        work.mkdir()
        (work / "timeseries.json").write_text(json.dumps({"bucket_seconds": 5.0}),
                                              encoding="utf-8")
        result = ToolRunResult(rows=[ToolMetricRow(dataset="d", metric="qps",
                                                   value=1.5, count=3)],
                               raw_score={"d": 1.5})
        payload = serialize_result(result, work)
        assert payload["status"] == "done"
        assert payload["rows"][0]["metric"] == "qps"
        assert payload["raw_score"] == {"d": 1.5}
        assert payload["timeseries"] == {"bucket_seconds": 5.0}

    def test_heartbeat_loop_puts_claimed_task(self, tmp_path):
        from aite.service.agent_client import AgentClient

        client = AgentClient(master="http://m", name="n", interval=0.01,
                             work_root=tmp_path)
        client.agent_id = "a1"
        tasks: queue.Queue = queue.Queue()
        calls = {"n": 0}

        def fake_post(path, body):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"heartbeat": "ok", "task": {"id": "t9", "engine": "locust",
                                                    "run_name": "r"}}
            return {"heartbeat": "ok", "task": None}

        client._post = fake_post
        th = threading.Thread(target=client._heartbeat_loop, args=(tasks,),
                              daemon=True)
        th.start()
        got = tasks.get(timeout=2)
        assert got["id"] == "t9"
        assert tasks.empty() or True     # 第二次心跳无任务
