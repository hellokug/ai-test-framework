"""全局版本登记 REST 集成测试（GET/POST/DELETE /versions，V1-PLAN §10.6）。

model_version 新表由 create_all 自动建；(model, version) 唯一 → 重复 409。
"""

import pytest


class TestVersionsApi:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def test_create_list_filter_delete(self, client):
        r = client.post("/api/v1/versions", json={
            "model": "deepseek", "version": "v1.3.0", "description": "迭代一"})
        assert r.status_code == 201
        body = r.json()
        assert body["model"] == "deepseek" and body["version"] == "v1.3.0"

        client.post("/api/v1/versions", json={"model": "qwen", "version": "v0.9"})
        assert client.post("/api/v1/versions", json={
            "model": "deepseek", "version": "v1.3.0"}).status_code == 409

        items = client.get("/api/v1/versions").json()["items"]
        assert len(items) == 2
        only = client.get("/api/v1/versions", params={"model": "deepseek"}).json()
        assert only["count"] == 1 and only["items"][0]["version"] == "v1.3.0"

        vid = only["items"][0]["id"]
        assert client.delete(f"/api/v1/versions/{vid}").status_code == 200
        assert client.delete(f"/api/v1/versions/{vid}").status_code == 404
        assert client.get("/api/v1/versions",
                          params={"model": "deepseek"}).json()["count"] == 0

    def test_create_validation_422(self, client):
        assert client.post("/api/v1/versions", json={
            "model": "", "version": "v1"}).status_code == 422
        assert client.post("/api/v1/versions", json={
            "model": "m", "version": "  "}).status_code == 422
