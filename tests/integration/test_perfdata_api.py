"""性能场景数据集 REST 集成测试（模块 J：POST 生成 / GET 清单 / 干跑校验）。"""

from pathlib import Path

import pytest


def _make_corpus(tmp_path) -> str:
    lines = [f"第{i:02d}条业务语料，用于压测负载的确定性生成与长度分桶验证测试。"
             for i in range(20)]
    p = tmp_path / "corpus.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


class TestPerfDataApi:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from aite import config
        from aite.web.app import create_app

        monkeypatch.setattr(config, "DEFAULT_PERFDATA_DIR",
                            str(tmp_path / "perf_datasets"))
        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def test_generate_and_list(self, client, tmp_path):
        corpus = _make_corpus(tmp_path)
        resp = client.post("/api/v1/perf/datasets", json={
            "corpus": corpus, "count": 6, "input_lengths": [80, 160],
            "prefix_sharing": 0.5, "seed": 42, "name": "rag-scene"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 6
        assert data["file"].startswith("rag-scene-")
        assert Path(data["path"]).is_file()
        assert data["stats"]["shared_ratio"] == 0.5
        assert all(len(p["input"]) <= 200 for p in data["preview"])

        listing = client.get("/api/v1/perf/datasets").json()
        assert listing["count"] == 1
        assert listing["items"][0]["file"] == data["file"]
        assert listing["items"][0]["stats"]["buckets"]

    def test_generate_invalid_config_400(self, client, tmp_path):
        corpus = _make_corpus(tmp_path)
        resp = client.post("/api/v1/perf/datasets", json={
            "corpus": corpus, "count": 0, "input_lengths": [80]})
        assert resp.status_code == 400
        assert "count" in resp.json()["detail"]

    def test_generate_missing_corpus_400(self, client):
        resp = client.post("/api/v1/perf/datasets", json={
            "corpus": "nope.txt", "count": 5, "input_lengths": [80]})
        assert resp.status_code == 400

    def test_validate_endpoint(self, client, tmp_path):
        corpus = _make_corpus(tmp_path)
        ok = client.post("/api/v1/perf/datasets/validate", json={
            "corpus": corpus, "count": 5, "input_lengths": [80]})
        assert ok.status_code == 200
        assert ok.json()["valid"] is True
        assert ok.json()["spec"]["seed"] == 42
        bad = client.post("/api/v1/perf/datasets/validate", json={
            "corpus": corpus, "count": 5, "input_lengths": [80],
            "prefix_sharing": 7})
        assert bad.status_code == 400
        assert "prefix_sharing" in bad.json()["detail"]
