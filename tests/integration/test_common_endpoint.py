"""GET /api/v1/common/models：模型预制清单（data/common.yaml 只读）。"""

import pytest


class TestCommonModels:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from aite import config
        from aite.web.app import create_app

        common = tmp_path / "common.yaml"
        common.write_text(
            "models:\n"
            "  deepseek:\n"
            "    model: deepseek-chat\n"
            "    base_url: https://api.deepseek.com/v1\n"
            "    api_key_env: DEEPSEEK_API_KEY\n",
            encoding="utf-8")
        monkeypatch.setattr(config, "DEFAULT_COMMON_FILE", str(common))
        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def test_list_profiles(self, client):
        data = client.get("/api/v1/common/models").json()
        assert data["count"] == 1
        assert data["items"][0]["name"] == "deepseek"
        assert data["items"][0]["model"] == "deepseek-chat"
        assert data["items"][0]["api_key_env"] == "DEEPSEEK_API_KEY"

    def test_missing_common_file_returns_empty(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from aite import config
        from aite.web.app import create_app

        monkeypatch.setattr(config, "DEFAULT_COMMON_FILE",
                            str(tmp_path / "nope.yaml"))
        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            data = c.get("/api/v1/common/models").json()
        assert data == {"items": [], "count": 0}
