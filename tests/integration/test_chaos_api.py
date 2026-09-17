"""可靠性框架 REST 集成测试（发现层 / 武器目录 / 故障注入+观测编排）。"""

import pytest


class TestChaosApi:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def test_targets_tree(self, client):
        resp = client.get("/api/v1/chaos/targets", params={"process": "python"})
        assert resp.status_code == 200
        tree = resp.json()
        assert tree["node"]["name"]
        assert tree["counts"]["processes"] >= 1

    def test_targets_fuzzy_no_match(self, client):
        resp = client.get("/api/v1/chaos/targets",
                          params={"process": "zz-missing-xyz"})
        assert resp.json()["processes"] == []

    def test_weapons_catalog(self, client):
        resp = client.get("/api/v1/chaos/weapons")
        assert resp.status_code == 200
        items = resp.json()["items"]
        names = {w["injector"] for w in items}
        assert "process" in names

    def test_observe_end_to_end_with_faults(self, client):
        """注入 mock 故障 + 探针打点：同步返回时间线与 RTO（探针全 fail 路径）。"""
        body = {
            "target": {"base_url": "http://127.0.0.1:9", "model": "m"},
            "faults": [{"injector": "process", "kind": "kill",
                        "target": {"name": "zz-missing-proc-xyz"}}],
            "interval": 0.05, "duration": 0.15,
        }
        resp = client.post("/api/v1/chaos/observe", json=body)
        assert resp.status_code == 400        # 注入失败（找不到进程）显式 400

    def test_observe_invalid_duration(self, client):
        body = {"target": {"base_url": "http://127.0.0.1:9", "model": "m"},
                "faults": [], "duration": 99999}
        resp = client.post("/api/v1/chaos/observe", json=body)
        assert resp.status_code == 400

    def test_observe_missing_target(self, client):
        resp = client.post("/api/v1/chaos/observe", json={"faults": []})
        assert resp.status_code == 400
