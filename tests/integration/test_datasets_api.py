"""数据集统一目录 REST 集成测试（docs/domain-backends.md §12.3 D3 平台切片）。

perf 型登记经既有 POST /perf/datasets 自动产生；功能型摄取走
/datasets/functional/import（harness.Case 真实校验），文件区落 tmp 目录。
"""

import json

import pytest


def _make_corpus(tmp_path) -> str:
    lines = [f"第{i:02d}条业务语料，用于压测负载的确定性生成与长度分桶验证测试。"
             for i in range(20)]
    p = tmp_path / "corpus.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def _case_line(case_id: str = "c1") -> str:
    return json.dumps({"id": case_id, "desc": "最小校验用例",
                       "messages": [{"role": "user", "content": "hi"}],
                       "expect": {"nonempty": True}}, ensure_ascii=False)


class TestDatasetsApi:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from aite import config
        from aite.functional import ingest
        from aite.web.app import create_app

        monkeypatch.setattr(config, "DEFAULT_PERFDATA_DIR",
                            str(tmp_path / "perf_datasets"))
        monkeypatch.setattr(ingest, "FUNCTIONAL_ROOT", tmp_path / "func_ds")
        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def _seed_perf_scene(self, client, tmp_path, name="scene-a"):
        resp = client.post("/api/v1/perf/datasets", json={
            "corpus": _make_corpus(tmp_path), "count": 6,
            "input_lengths": [80], "seed": 42, "name": name})
        assert resp.status_code == 200
        return resp.json()

    def test_perf_generation_auto_registered_and_listed(self, client, tmp_path):
        self._seed_perf_scene(client, tmp_path, name="scene-a")

        listing = client.get("/api/v1/datasets", params={"type": "perf"}).json()
        assert listing["count"] == 1
        row = listing["items"][0]
        assert row["type"] == "perf" and row["name"] == "scene-a"
        assert row["version"] == "seed42"
        assert row["stats"]["buckets"]

        # type 过滤隔离
        assert client.get("/api/v1/datasets",
                          params={"type": "functional"}).json()["count"] == 0

    def test_detail_via_type_name_ref(self, client, tmp_path):
        self._seed_perf_scene(client, tmp_path, name="scene-a")
        detail = client.get("/api/v1/datasets/perf:scene-a")
        assert detail.status_code == 200
        assert detail.json()["name"] == "scene-a"

    def test_delete_keeps_files_semantics(self, client, tmp_path):
        generated = self._seed_perf_scene(client, tmp_path, name="scene-b")
        assert client.delete("/api/v1/datasets/perf:scene-b").json()["deleted"] is True
        assert client.get("/api/v1/datasets/perf:scene-b").status_code == 404
        # 登记删除 ≠ 文件删除（与 CLI rm 同语义）
        from pathlib import Path
        assert Path(generated["path"]).is_file()

    def test_list_unknown_type_422_and_unknown_ref_404(self, client):
        assert client.get("/api/v1/datasets",
                          params={"type": "bad"}).status_code == 422
        assert client.get("/api/v1/datasets/nope").status_code == 404

    def test_functional_import_and_force(self, client):
        resp = client.post("/api/v1/datasets/functional/import", json={
            "name": "mini", "jsonl": _case_line(), "notes": "测试摄取"})
        assert resp.status_code == 201
        entry = resp.json()
        assert entry["type"] == "functional" and entry["name"] == "mini"
        assert entry["stats"]["count"] == 1

        # 同名不带 force → 422；带 force → 覆盖登记
        dup = client.post("/api/v1/datasets/functional/import", json={
            "name": "mini", "jsonl": _case_line()})
        assert dup.status_code == 422
        ok = client.post("/api/v1/datasets/functional/import", json={
            "name": "mini", "jsonl": _case_line("c2"), "force": True})
        assert ok.status_code == 201
        assert ok.json()["stats"]["count"] == 1

        # 摄取后目录可查、详情可达
        assert client.get("/api/v1/datasets",
                          params={"type": "functional"}).json()["count"] == 1
        assert client.get("/api/v1/datasets/functional:mini").status_code == 200

    def test_functional_import_bad_case_422(self, client):
        bad = json.dumps({"id": "c1", "expect": {}})   # 缺 desc/messages
        resp = client.post("/api/v1/datasets/functional/import", json={
            "name": "bad", "jsonl": bad})
        assert resp.status_code == 422
        # 坏数据不留登记
        assert client.get("/api/v1/datasets/functional:bad").status_code == 404
