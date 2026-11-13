"""精度域场景/脚本 REST 集成测试（/accuracy/scenarios、/accuracy/scripts）。

托管场景根 monkeypatch 到 tmp（场景与脚本全部用户自管，无预置）；错误映射
404/409/422 与 functional 路由同口径；条数口径覆盖自研域落定与工具域未知。
"""

import json

import pytest


def _spec():
    return {
        "module": "accuracy", "run_name": "it-baseline", "engine": "evalscope",
        "target": {"profile": "deepseek"},
        "tools": {"datasets": ["gsm8k"], "limit_per_subset": 5},
        "report": ["json"], "output_dir": "./reports",
    }


def _selfbuilt_spec():
    return {
        "module": "accuracy", "run_name": "it-agent-mini",
        "target": {"type": "custom_http", "base_url": "http://x",
                   "extract": "output"},
        "datasets": [{"type": "builtin", "id": "gsm8k"}],
        "metrics": [{"contains": {}}],
    }


class TestAccuracyAssetsApi:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from aite import config
        from aite.web.app import create_app

        scen_root = tmp_path / "acc-scen"
        monkeypatch.setattr(config, "DEFAULT_ACCURACY_SCENARIOS_DIR",
                            str(scen_root))
        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c, scen_root

    def _make_scenario(self, client, sid="s1", name="场景一", dimension=""):
        resp = client.post("/api/v1/accuracy/scenarios",
                           json={"id": sid, "display_name": name,
                                 "dimension": dimension})
        assert resp.status_code == 201
        return resp.json()

    # ------------------------------------------------------------- 场景
    def test_no_preset_assets(self, client):
        """场景与脚本无预置：空根 → 双空清单。"""
        c, _ = client
        assert c.get("/api/v1/accuracy/scenarios").json() == {"items": [], "count": 0}
        assert c.get("/api/v1/accuracy/scripts").json() == {"items": [], "count": 0}

    def test_scenario_create_list_detail_delete(self, client, tmp_path):
        c, root = client
        made = self._make_scenario(c, dimension="Agent")
        assert made["id"] == "s1" and made["display_name"] == "场景一"
        assert made["dimension"] == "Agent"       # 共享标签库内置标签合法
        assert json.loads((root / "s1" / "manifest.json").read_text(
            encoding="utf-8"))["dimension"] == "Agent"
        items = c.get("/api/v1/accuracy/scenarios").json()["items"]
        assert [i["id"] for i in items] == ["s1"]
        dup = c.post("/api/v1/accuracy/scenarios", json={"id": "s1"})
        assert dup.status_code == 409
        bad = c.post("/api/v1/accuracy/scenarios", json={"id": "Bad Id"})
        assert bad.status_code == 422 and "英文编号" in bad.json()["detail"]
        unknown_dim = c.post("/api/v1/accuracy/scenarios",
                             json={"id": "dx", "dimension": "不存在的标签"})
        assert unknown_dim.status_code == 422 and "未知维度标签" in unknown_dim.json()["detail"]
        assert c.delete("/api/v1/accuracy/scenarios/s1").status_code == 200
        assert c.get("/api/v1/accuracy/scenarios/s1").status_code == 404

    def test_scenario_delete_gated_then_force_cascade(self, client):
        """删除保护：有脚本默认 409 拒删；force=true 二次确认级联整目录。"""
        c, root = client
        self._make_scenario(c, sid="withscripts")
        body = {"scenario_id": "withscripts", "id": "sc_a", "name": "脚本A",
                "spec": _spec()}
        assert c.post("/api/v1/accuracy/scripts", json=body).status_code == 201
        gated = c.delete("/api/v1/accuracy/scenarios/withscripts")
        assert gated.status_code == 409
        assert "仍有 1 个脚本" in gated.json()["detail"]
        assert (root / "withscripts").exists()      # 拒删 = 目录原封不动
        ok = c.delete("/api/v1/accuracy/scenarios/withscripts",
                      params={"force": "true"})
        assert ok.status_code == 200
        assert not (root / "withscripts").exists()  # 级联：整目录移除
        assert c.get("/api/v1/accuracy/scripts/sc_a").status_code == 404

    # ------------------------------------------------------------- 脚本 CRUD
    def test_script_create_list_slim_get_patch_delete(self, client):
        c, root = client
        self._make_scenario(c)
        resp = c.post("/api/v1/accuracy/scripts", json={
            "scenario_id": "s1", "id": "it-custom", "name": "集成基线",
            "description": "版本基线回归", "spec": _spec()})
        assert resp.status_code == 201
        assert (root / "s1" / "scripts" / "it-custom.yaml").is_file()

        items = c.get("/api/v1/accuracy/scripts").json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["id"] == "it-custom" and item["name"] == "集成基线"
        assert item["scenario"] == "s1" and item["scenario_name"] == "场景一"
        assert item["datasets"] == ["gsm8k"]
        assert item["case_count"] is None          # 工具域基准条数本地不可知
        assert "description" not in item and "spec" not in item   # 清单瘦身
        scoped = c.get("/api/v1/accuracy/scripts",
                       params={"scenario": "other"}).json()["items"]
        assert scoped == []

        got = c.get("/api/v1/accuracy/scripts/it-custom").json()
        assert got["description"] == "版本基线回归"   # 描述仅详情带
        assert got["spec"]["tools"]["datasets"] == ["gsm8k"]

        patched = c.patch("/api/v1/accuracy/scripts/it-custom",
                          json={"name": "新名"})
        assert patched.status_code == 200 and patched.json()["name"] == "新名"
        assert c.delete("/api/v1/accuracy/scripts/it-custom").status_code == 200
        assert c.get("/api/v1/accuracy/scripts/it-custom").status_code == 404

    def test_selfbuilt_script_case_count_resolved(self, client):
        """自研域 builtin 引用 → 打包样本行数落定。"""
        from aite.datasets import builtin

        c, _ = client
        self._make_scenario(c, sid="sb")
        resp = c.post("/api/v1/accuracy/scripts", json={
            "scenario_id": "sb", "id": "pack", "name": "打包样本",
            "spec": _selfbuilt_spec()})
        assert resp.status_code == 201
        expected = len(builtin._read_entry(builtin.merged_info()["gsm8k"]))
        assert resp.json()["case_count"] == expected > 0

    def test_script_create_missing_scenario_404(self, client):
        c, _ = client
        resp = c.post("/api/v1/accuracy/scripts", json={
            "scenario_id": "no-such", "id": "x1", "name": "n", "spec": _spec()})
        assert resp.status_code == 404

    def test_script_id_unique_across_scenarios_409(self, client):
        """脚本 id 全平台唯一：跨场景同 id → 409。"""
        c, _ = client
        self._make_scenario(c, sid="s1")
        self._make_scenario(c, sid="s2", name="场景二")
        body = {"id": "dup", "name": "A", "spec": _spec()}
        assert c.post("/api/v1/accuracy/scripts",
                      json={**body, "scenario_id": "s1"}).status_code == 201
        assert c.post("/api/v1/accuracy/scripts",
                      json={**body, "scenario_id": "s2"}).status_code == 409

    def test_save_validation_422(self, client):
        c, _ = client
        self._make_scenario(c)
        assert c.post("/api/v1/accuracy/scripts", json={
            "scenario_id": "s1", "id": "Bad Id", "spec": _spec()}).status_code == 422
        assert c.post("/api/v1/accuracy/scripts", json={
            "scenario_id": "s1", "id": "ok-id",
            "spec": {"module": "accuracy"}}).status_code == 422
        bad = _selfbuilt_spec()
        bad["bogus_key"] = 1
        assert c.post("/api/v1/accuracy/scripts", json={
            "scenario_id": "s1", "id": "bad2", "spec": bad}).status_code == 422

    def test_scenario_detail_lists_script_entries(self, client):
        c, _ = client
        self._make_scenario(c)
        c.post("/api/v1/accuracy/scripts", json={
            "scenario_id": "s1", "id": "e1", "name": "条目", "spec": _spec()})
        detail = c.get("/api/v1/accuracy/scenarios/s1").json()
        assert detail["scripts"] == 1
        assert detail["script_entries"][0]["id"] == "e1"
        assert "spec" not in detail["script_entries"][0]   # 条目为瘦身口径
