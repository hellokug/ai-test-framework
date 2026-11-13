"""全局版本登记 REST 集成测试（GET/POST/DELETE /versions，V1-PLAN §10.6）。

版本登记与模型无关（model 为遗留列，新登记恒为空串）：版本号唯一 → 重复 409。
model_version 表由 create_all 自动建。
"""

import logging

import pytest


class TestVersionsApi:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from aite.web.app import create_app

        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def test_create_list_delete(self, client):
        r = client.post("/api/v1/versions", json={
            "version": "v1.3.0", "description": "迭代一"})
        assert r.status_code == 201
        body = r.json()
        assert body["version"] == "v1.3.0" and body["model"] == ""

        client.post("/api/v1/versions", json={"version": "v0.9"})
        assert client.post("/api/v1/versions", json={
            "version": "v1.3.0"}).status_code == 409
        assert client.post("/api/v1/versions", json={   # 旧客户端传 model 也不影响唯一性
            "model": "qwen", "version": "v1.3.0"}).status_code == 409

        items = client.get("/api/v1/versions").json()["items"]
        assert len(items) == 2

        vid = items[0]["id"]
        assert client.delete(f"/api/v1/versions/{vid}").status_code == 200
        assert client.delete(f"/api/v1/versions/{vid}").status_code == 404
        assert client.get("/api/v1/versions").json()["count"] == 1

    def test_create_validation_422(self, client):
        assert client.post("/api/v1/versions", json={
            "version": "  "}).status_code == 422
        assert client.post("/api/v1/versions", json={}).status_code == 422

    def test_422_logged_with_masked_body(self, client, caplog):
        """422 详情进服务端日志（字段路径 + 打码 body），响应格式不变。"""
        with caplog.at_level(logging.WARNING, logger="aite.web"):
            r = client.post("/api/v1/versions", json={
                "api_key": "sk-secret123", "description": ""})
        assert r.status_code == 422
        assert r.json()["detail"][0]["loc"] == ["body", "version"]
        text = caplog.text
        assert "body.version" in text and "missing" in text
        assert "api_key" in text and "sk-secret123" not in text
