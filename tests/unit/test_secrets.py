"""本地密钥库测试：加解密回环、解析优先级（环境变量 > 密钥库）、掩码、API。"""

import json

import pytest


@pytest.fixture()
def secret_env(tmp_path, monkeypatch):
    from aite import config

    monkeypatch.setattr(config, "SECRET_KEY_FILE", str(tmp_path / ".secret.key"))
    monkeypatch.setattr(config, "SECRET_STORE_FILE", str(tmp_path / ".secrets.json"))
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    return tmp_path


class TestSecretStore:
    def test_encrypt_decrypt_roundtrip(self, secret_env):
        from aite.utils import secrets as store

        token = store.encrypt("sk-abcdef123456")
        assert token != "sk-abcdef123456" and "sk-" not in token
        assert store.decrypt(token) == "sk-abcdef123456"
        assert store.encrypt("sk-abcdef123456") != token   # 随机 nonce → 密文不重复

    def test_set_list_delete(self, secret_env):
        from aite.utils import secrets as store

        mask = store.set_secret("DEEPSEEK_API_KEY", "sk-abcdef123456")
        assert "****" in mask and mask.startswith("sk-") and mask.endswith("56")
        items = store.list_secrets()
        assert len(items) == 1 and items[0]["name"] == "DEEPSEEK_API_KEY"
        assert "enc" not in json.dumps(items)               # 清单永不回传明文/密文
        assert store.delete_secret("DEEPSEEK_API_KEY") is True
        assert store.list_secrets() == []

    def test_persist_across_instances(self, secret_env):
        from aite.utils import secrets as store

        store.set_secret("K", "v1")
        mask = store.set_secret("K", "v2")                  # 同名覆盖
        assert mask == "****"
        assert len(store.list_secrets()) == 1


class TestResolveSecret:
    def test_env_var_takes_priority(self, secret_env, monkeypatch):
        from aite.utils import secrets as store

        store.set_secret("TEST_API_KEY", "from-store")
        monkeypatch.setenv("TEST_API_KEY", "from-env")
        assert store.resolve_secret("TEST_API_KEY") == "from-env"

    def test_store_fallback_and_default(self, secret_env):
        from aite.utils import secrets as store

        store.set_secret("TEST_API_KEY", "from-store")
        assert store.resolve_secret("TEST_API_KEY") == "from-store"
        assert store.resolve_secret("NOPE", "EMPTY") == "EMPTY"
        assert store.resolve_secret("NOPE") is None

    def test_corrupted_entry_degrades_to_default(self, secret_env):
        import json

        from aite.utils import secrets as store

        store_file = secret_env / ".secrets.json"
        store_file.write_text(json.dumps(
            {"TEST_API_KEY": {"enc": "!!!corrupted!!!", "mask": "****"}}),
            encoding="utf-8")
        assert store.resolve_secret("TEST_API_KEY", "EMPTY") == "EMPTY"


class TestSecretsApi:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from aite import config
        from aite.web.app import create_app

        monkeypatch.setattr(config, "SECRET_KEY_FILE", str(tmp_path / ".secret.key"))
        monkeypatch.setattr(config, "SECRET_STORE_FILE", str(tmp_path / ".secrets.json"))
        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def test_put_list_delete(self, client):
        put = client.put("/api/v1/secrets/DEEPSEEK_API_KEY",
                         json={"value": "sk-abcdef123456"})
        assert put.status_code == 200
        assert "****" in put.json()["mask"]
        items = client.get("/api/v1/secrets").json()["items"]
        assert len(items) == 1 and items[0]["mask"] == put.json()["mask"]
        raw = json.dumps(items)
        assert "sk-abcdef123456" not in raw                  # 明文/密文永不回传
        assert client.delete("/api/v1/secrets/DEEPSEEK_API_KEY").json()["deleted"]
        assert client.get("/api/v1/secrets").json()["count"] == 0

    def test_put_empty_value_422(self, client):
        resp = client.put("/api/v1/secrets/EMPTY_NAME", json={"value": ""})
        assert resp.status_code == 422
