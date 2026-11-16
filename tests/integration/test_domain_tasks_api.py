"""四域任务 REST 集成测试（docs/domain-backends.md §2 契约的 Web 切片）。

用必失败的 spec 驱动状态机（resolve/校验 fail-fast）验证 run→failed→同任务重跑；
执行历史逐轮累积（retry 不再新建任务）；CRUD 与守卫语义逐条对契约。
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
        # pending 不允许 stop（仅 running）；stop body（mode）合法透传到 kit
        # （非法 mode / 非法状态都映射 409，而非 422 解析失败）
        assert client.post(f"/api/v1/perf/tasks/{task['id']}/stop").status_code == 409
        assert client.post(f"/api/v1/perf/tasks/{task['id']}/stop",
                           json={"mode": "force"}).status_code == 409
        assert client.post(f"/api/v1/perf/tasks/{task['id']}/stop",
                           json={"mode": "bogus"}).status_code == 409
        # 非终态无 result
        result = client.get(f"/api/v1/perf/tasks/{task['id']}/result")
        assert result.status_code == 409

    def test_name_required_and_unique(self, client):
        """名称必填（缺失/空 → 422）且同域唯一（占用 → 409）；跨域同名不受限。"""
        assert client.post("/api/v1/perf/tasks",
                           json={"spec": {}}).status_code == 422
        assert client.post("/api/v1/perf/tasks",
                           json={"name": "  ", "spec": {}}).status_code == 422
        assert client.post("/api/v1/perf/tasks",
                           json={"name": "u1", "spec": {}}).status_code == 200
        assert client.post("/api/v1/perf/tasks",
                           json={"name": "u1", "spec": {}}).status_code == 409
        assert client.post("/api/v1/accuracy/tasks",
                           json={"name": "u1", "spec": {}}).status_code == 200
        other = client.post("/api/v1/perf/tasks",
                            json={"name": "u2", "spec": {}}).json()
        assert client.patch(f"/api/v1/perf/tasks/{other['id']}",
                            json={"name": "u1"}).status_code == 409
        assert client.patch(f"/api/v1/perf/tasks/{other['id']}",
                            json={"name": "u2"}).status_code == 200
        assert client.patch(f"/api/v1/perf/tasks/{other['id']}",
                            json={"name": ""}).status_code == 422

    def test_duplicate_dedups_copy_name(self, client):
        """连复制同名源：副本名递增「· 副本」「· 副本 2」，不落 409。"""
        task = self._create(client, "perf", {"engine": "pytest"}, name="src2")
        d1 = client.post(f"/api/v1/perf/tasks/{task['id']}/duplicate").json()
        d2 = client.post(f"/api/v1/perf/tasks/{task['id']}/duplicate").json()
        assert [d1["name"], d2["name"]] == ["src2 · 副本", "src2 · 副本 2"]

    def test_unknown_task_404(self, client):
        assert client.get("/api/v1/perf/tasks/nope").status_code == 404
        assert client.patch("/api/v1/perf/tasks/nope",
                            json={"name": "x"}).status_code == 404
        assert client.delete("/api/v1/perf/tasks/nope").status_code == 404
        assert client.post("/api/v1/perf/tasks/nope/run").status_code == 404

    def test_perf_sla_ramp_spec_fail_fast(self, client):
        """SLA 爬坡 spec 校验 fail-fast：未知 SLA 指标 → 任务 failed，
        error 直达根因（校验在 adapter build_config，经 API 错误通道上浮）。"""
        task = self._create(client, "perf", {
            "engine": "evalscope-perf",
            "target": {"type": "openai_compatible",
                       "base_url": "http://x/v1", "model": "m"},
            "perf": {"prompt": "p", "mode": "ramp",
                     "ramp": {"from": 1, "to": 4, "step": 3,
                              "sla": {"nope": 1}}}}, name="sla-bad")
        run = client.post(f"/api/v1/perf/tasks/{task['id']}/run")
        assert run.status_code == 202
        final = self._wait_status(client, "perf", task["id"], {"failed"})
        assert "未知指标" in final["error"]

    @pytest.mark.parametrize("domain", ["functional", "accuracy", "perf"])
    def test_run_fail_fast_then_rerun(self, client, domain):
        """无效 suite spec（缺 datasets）fail-fast → failed；终态可改配置/重跑
        同一任务，执行历史逐轮累积（retry 不再新建任务）。"""
        task = self._create(client, domain, {"engine": "pytest"}, name="failing")
        run = client.post(f"/api/v1/{domain}/tasks/{task['id']}/run")
        assert run.status_code == 202
        assert run.json()["status"] == "running"

        final = self._wait_status(client, domain, task["id"], {"failed"})
        assert final["error"]

        result = client.get(f"/api/v1/{domain}/tasks/{task['id']}/result")
        assert result.status_code == 200
        assert result.json()["status"] == "failed"

        # 终态可改配置（仅运行中禁止）——「改参再跑」的前提
        patched = client.patch(f"/api/v1/{domain}/tasks/{task['id']}",
                               json={"name": "failing-2"})
        assert patched.status_code == 200

        # 终态可直接重跑（同一任务，不再新建）
        rerun = client.post(f"/api/v1/{domain}/tasks/{task['id']}/run")
        assert rerun.status_code == 202
        assert rerun.json()["id"] == task["id"]
        self._wait_status(client, domain, task["id"], {"failed"})

        # retry = 同任务再跑一轮（202 返回同一任务快照）
        retry = client.post(f"/api/v1/{domain}/tasks/{task['id']}/retry")
        assert retry.status_code == 202
        same = retry.json()
        assert same["id"] == task["id"]
        assert same["retry_of"] is None and same["retry_count"] == 0
        self._wait_status(client, domain, task["id"], {"failed"})

        # 执行历史：3 轮逐轮累积，每轮带 trigger/当时 spec 快照/起止时间
        detail = client.get(f"/api/v1/{domain}/tasks/{task['id']}").json()
        hist = detail["history"]
        assert [h["n"] for h in hist] == [1, 2, 3]
        assert [h["trigger"] for h in hist] == ["run", "run", "retry"]
        assert all(h["status"] == "failed" for h in hist)
        assert all(h["spec"] == task["spec"] for h in hist)
        assert all(h["started_at"] and h["finished_at"] for h in hist)
        # 任务不裂变：该域列表仍只有这一个任务
        listing = client.get(f"/api/v1/{domain}/tasks").json()
        assert listing["count"] == 1
        assert listing["items"][0]["id"] == task["id"]

        # 终态任务亦可复制（独立分支变体：新建任务、无历史链）
        dup = client.post(f"/api/v1/{domain}/tasks/{task['id']}/duplicate")
        assert dup.status_code == 201
        assert dup.json()["status"] == "pending"
        assert dup.json()["retry_of"] is None
        assert dup.json()["history"] is None

    @pytest.mark.parametrize("domain", ["functional", "accuracy", "perf"])
    def test_duplicate_copies_spec_as_pending(self, client, domain):
        """复制 = 同 spec 新 pending 任务、名字加「· 副本」、无 retry 链，原任务不动。"""
        task = self._create(client, domain, {"engine": "pytest"}, name="src")
        dup = client.post(f"/api/v1/{domain}/tasks/{task['id']}/duplicate")
        assert dup.status_code == 201
        new_task = dup.json()
        assert new_task["id"] != task["id"]
        assert new_task["status"] == "pending"
        assert new_task["spec"] == task["spec"]
        assert new_task["name"] == "src · 副本"
        assert new_task["retry_of"] is None
        assert new_task["retry_count"] == 0
        # 原任务不受影响（pending、spec/name 原样）
        original = client.get(f"/api/v1/{domain}/tasks/{task['id']}").json()
        assert original["status"] == "pending"
        assert original["name"] == "src"
        assert original["spec"] == task["spec"]
        assert client.post(f"/api/v1/{domain}/tasks/nope/duplicate").status_code == 404

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
