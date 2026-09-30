"""四域任务 REST 集成测试（docs/domain-backends.md §2 契约的 Web 切片）。

用必失败的 spec 驱动状态机（resolve/校验 fail-fast）验证 run→failed→retry 链；
CRUD 与守卫语义（pending 不可删 / 非终态无 result / stop 仅 running）逐条对契约。
"""

import time

import pytest

DOMAINS = ["functional", "accuracy", "perf", "reliability"]


class TestDomainTasksApi:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def _create(self, client, domain, spec, name="t1"):
        resp = client.post(f"/api/v1/{domain}/tasks",
                           json={"name": name, "spec": spec})
        assert resp.status_code == 200
        return resp.json()

    def _wait_status(self, client, domain, task_id, statuses, timeout=15.0):
        deadline = time.time() + timeout
        task = {}
        while time.time() < deadline:
            task = client.get(f"/api/v1/{domain}/tasks/{task_id}").json()
            if task.get("status") in statuses:
                return task
            time.sleep(0.1)
        raise AssertionError(f"任务未到达 {statuses}：{task}")

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_four_domains_mounted(self, client, domain):
        task = self._create(client, domain, {}, name=f"mount-{domain}")
        assert task["status"] == "pending"
        assert task["name"] == f"mount-{domain}"

    def test_crud_and_guards(self, client):
        task = self._create(client, "perf", {"engine": "pytest"})
        assert task["spec"] == {"engine": "pytest"}

        listing = client.get("/api/v1/perf/tasks").json()
        assert listing["count"] == 1
        assert listing["items"][0]["id"] == task["id"]

        patched = client.patch(f"/api/v1/perf/tasks/{task['id']}",
                               json={"name": "renamed"})
        assert patched.status_code == 200
        assert patched.json()["name"] == "renamed"

        # pending 非终态：删除 409
        assert client.delete(f"/api/v1/perf/tasks/{task['id']}").status_code == 409
        # pending 不允许 stop（仅 running）
        assert client.post(f"/api/v1/perf/tasks/{task['id']}/stop").status_code == 409
        # 非终态无 result
        result = client.get(f"/api/v1/perf/tasks/{task['id']}/result")
        assert result.status_code == 409

    def test_unknown_task_404(self, client):
        assert client.get("/api/v1/perf/tasks/nope").status_code == 404
        assert client.patch("/api/v1/perf/tasks/nope",
                            json={"name": "x"}).status_code == 404
        assert client.delete("/api/v1/perf/tasks/nope").status_code == 404
        assert client.post("/api/v1/perf/tasks/nope/run").status_code == 404

    @pytest.mark.parametrize("domain", ["functional", "accuracy", "perf"])
    def test_run_fail_fast_then_retry(self, client, domain):
        """无效 suite spec（缺 datasets）fail-fast → failed → result/retry 链。"""
        task = self._create(client, domain, {"engine": "pytest"}, name="failing")
        run = client.post(f"/api/v1/{domain}/tasks/{task['id']}/run")
        assert run.status_code == 202
        assert run.json()["status"] == "running"

        final = self._wait_status(client, domain, task["id"], {"failed"})
        assert final["error"]

        result = client.get(f"/api/v1/{domain}/tasks/{task['id']}/result")
        assert result.status_code == 200
        assert result.json()["status"] == "failed"

        # failed 后不允许再 run（仅 pending）
        assert client.post(f"/api/v1/{domain}/tasks/{task['id']}/run").status_code == 409

        retry = client.post(f"/api/v1/{domain}/tasks/{task['id']}/retry")
        assert retry.status_code == 201
        new_task = retry.json()
        assert new_task["id"] != task["id"]
        assert new_task["retry_of"] == task["id"]
        assert new_task["retry_count"] == 1
        assert new_task["status"] == "pending"
        # retry 链可查
        listing = client.get(f"/api/v1/{domain}/tasks",
                             params={"retry_of": task["id"]}).json()
        assert listing["count"] == 1

    def test_reliability_invalid_spec_fails(self, client):
        """观测 spec 缺 target → 校验 fail-fast → failed。"""
        task = self._create(client, "reliability", {}, name="obs")
        assert client.post(f"/api/v1/reliability/tasks/{task['id']}/run").status_code == 202
        final = self._wait_status(client, "reliability", task["id"], {"failed"})
        assert "target" in final["error"]

    def test_status_filter(self, client):
        self._create(client, "perf", {}, name="p1")
        self._create(client, "perf", {}, name="p2")
        listing = client.get("/api/v1/perf/tasks", params={"status": "pending"}).json()
        assert listing["count"] == 2
        assert client.get("/api/v1/perf/tasks",
                          params={"status": "running"}).json()["count"] == 0

    def test_delete_terminal_task(self, client):
        task = self._create(client, "functional", {"engine": "pytest"}, name="del")
        client.post(f"/api/v1/functional/tasks/{task['id']}/run")
        self._wait_status(client, "functional", task["id"], {"failed"})
        assert client.delete(f"/api/v1/functional/tasks/{task['id']}").json()["deleted"] is True
        assert client.get(f"/api/v1/functional/tasks/{task['id']}").status_code == 404


class TestDomainFilterEndpoints:
    """/runs 与 /stats 的 domain 参数接线（过滤逻辑单测另文件覆盖）。"""

    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def test_runs_domain_param(self, client):
        ok = client.get("/api/v1/runs", params={"domain": "accuracy"})
        assert ok.status_code == 200
        assert ok.json() == {"items": [], "count": 0}
        assert client.get("/api/v1/runs",
                          params={"domain": "nope"}).status_code == 422

    def test_stats_domain_param(self, client):
        ok = client.get("/api/v1/stats", params={"domain": "perf"}).json()
        assert ok["runs_total"] == 0
        assert client.get("/api/v1/stats",
                          params={"domain": "nope"}).status_code == 422
