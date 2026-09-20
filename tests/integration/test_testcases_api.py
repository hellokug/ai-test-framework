"""文字用例库 REST 集成测试（§15，T1）：CRUD / 绑定校验 / 编号自增 / 404 语义。"""

import pytest


class TestTestCaseDocsApi:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def test_create_list_patch_delete(self, client):
        body = {"domain": "reliability", "title": "kill worker 后 RTO 达标",
                "purpose": "验证进程被杀后的恢复能力",
                "expected": "RTO ≤ 30s", "priority": "P0", "risk": "高",
                "script": {"kind": "suite_case", "case_name": "kill-worker"}}
        resp = client.post("/api/v1/test-cases", json=body)
        assert resp.status_code == 200, resp.text
        row = resp.json()
        assert row["case_no"] == "TC-REL-0001" and row["status"] == "draft"
        assert row["script"]["case_name"] == "kill-worker"

        listed = client.get("/api/v1/test-cases",
                            params={"domain": "reliability",
                                    "priority": "P0"}).json()
        assert listed["count"] == 1

        patched = client.patch(f"/api/v1/test-cases/{row['case_no']}",
                               json={"status": "active"})
        assert patched.json()["status"] == "active"

        runs = client.get(
            f"/api/v1/test-cases/{row['case_no']}/runs").json()
        assert runs["count"] == 0

        deleted = client.delete(f"/api/v1/test-cases/{row['case_no']}")
        assert deleted.json()["deleted"] is True
        assert client.get(
            f"/api/v1/test-cases/{row['case_no']}").status_code == 404

    def test_case_no_auto_increment_per_domain(self, client):
        for domain, prefix in (("reliability", "TC-REL-0001"),
                               ("functional", "TC-FUNC-0001"),
                               ("reliability", "TC-REL-0002")):
            resp = client.post("/api/v1/test-cases",
                               json={"domain": domain, "title": f"t-{domain}"})
            assert resp.status_code == 200
            assert resp.json()["case_no"] == prefix

    def test_binding_validation_per_domain(self, client):
        resp = client.post("/api/v1/test-cases", json={
            "domain": "reliability", "title": "t",
            "script": {"kind": "scenario_case", "scenario": "s",
                       "case_id": "c"}})
        assert resp.status_code == 422 and "仅用于 functional" in resp.json()["detail"]
        resp = client.post("/api/v1/test-cases", json={
            "domain": "perf", "title": "t",
            "script": {"kind": "suite_metric"}})
        assert resp.status_code == 422 and "metric" in resp.json()["detail"]
        resp = client.post("/api/v1/test-cases",
                           json={"domain": "chaos", "title": "t"})
        assert resp.status_code == 422 and "domain" in resp.json()["detail"]

    def test_patch_unknown_and_domain_immutable(self, client):
        resp = client.patch("/api/v1/test-cases/TC-NOPE-9999",
                            json={"status": "active"})
        assert resp.status_code == 404
        body = client.post("/api/v1/test-cases",
                           json={"domain": "perf",
                                 "title": "t"}).json()
        resp = client.patch(f"/api/v1/test-cases/{body['case_no']}",
                            json={"domain": "reliability"})
        assert resp.status_code == 422 and "不可改" in resp.json()["detail"]
