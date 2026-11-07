"""精度域脚本 REST 集成测试（GET/POST/PATCH/DELETE /accuracy/scripts）。

自定义根 monkeypatch 到 tmp；builtin = 真实 examples/accuracy 只读扫描；
错误映射 404/409/422 与 functional 路由同口径。
"""

import pytest


def _spec():
    return {
        "module": "accuracy", "run_name": "it-baseline", "engine": "evalscope",
        "target": {"profile": "deepseek"},
        "tools": {"datasets": ["gsm8k"], "limit_per_subset": 5},
        "report": ["json"], "output_dir": "./reports",
    }


class TestAccuracyScriptsApi:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from aite import config
        from aite.web.app import create_app

        custom_root = tmp_path / "acc-scripts"
        monkeypatch.setattr(config, "DEFAULT_ACCURACY_SCRIPTS_DIR",
                            str(custom_root))
        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c, custom_root

    def test_list_includes_builtin_and_custom(self, client):
        c, root = client
        resp = c.get("/api/v1/accuracy/scripts")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert {i["source"] for i in items} >= {"builtin"}
        assert any(i["id"] == "agent_eval" for i in items)

        resp = c.post("/api/v1/accuracy/scripts",
                      json={"id": "it-custom", "name": "集成基线",
                            "description": "", "spec": _spec()})
        assert resp.status_code == 201
        assert (root / "it-custom.yaml").is_file()

        items = c.get("/api/v1/accuracy/scripts").json()["items"]
        assert any(i["id"] == "it-custom" and i["source"] == "custom"
                   for i in items)
        assert c.get("/api/v1/accuracy/scripts?source=builtin").json()["items"] \
            and all(i["source"] == "builtin" for i in
                    c.get("/api/v1/accuracy/scripts?source=builtin").json()["items"])

    def test_get_detail_404(self, client):
        c, _ = client
        assert c.get("/api/v1/accuracy/scripts/no-such").status_code == 404

    def test_patch_and_delete_custom(self, client):
        c, _ = client
        c.post("/api/v1/accuracy/scripts",
               json={"id": "it-edit", "name": "旧名", "spec": _spec()})
        resp = c.patch("/api/v1/accuracy/scripts/it-edit",
                       json={"name": "新名"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "新名"
        assert c.delete("/api/v1/accuracy/scripts/it-edit").status_code == 200
        assert c.get("/api/v1/accuracy/scripts/it-edit").status_code == 404

    def test_builtin_readonly_409(self, client):
        c, _ = client
        assert c.patch("/api/v1/accuracy/scripts/ceval",
                       json={"name": "改"}).status_code == 409
        assert c.delete("/api/v1/accuracy/scripts/ceval").status_code == 409

    def test_save_validation_422(self, client):
        c, _ = client
        assert c.post("/api/v1/accuracy/scripts",
                      json={"id": "Bad Id", "spec": _spec()}).status_code == 422
        assert c.post("/api/v1/accuracy/scripts",
                      json={"id": "ok-id", "spec": {"module": "accuracy"}}).status_code == 422
        assert c.post("/api/v1/accuracy/scripts",
                      json={"id": "ceval", "spec": _spec()}).status_code == 409
