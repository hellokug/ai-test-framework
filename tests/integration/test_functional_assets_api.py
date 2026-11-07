"""功能域评估场景 / 脚本 / 维度标签 REST 集成测试（V1-PLAN §10.4/§10.5）。

用户场景根 monkeypatch 到 tmp（builtin 场景为包内只读真实目录）；脚本 CRUD
验证派生 cases.jsonl 与 legacy 收编；标签库走临时 SQLite（create_all 建表）。
"""

import json

import pytest


def _case(case_id: str | None = None, content: str = "hi") -> dict:
    row = {"desc": f"用例 {case_id or content}",
           "messages": [{"role": "user", "content": content}],
           "expect": {"nonempty": True}}
    if case_id:
        row["id"] = case_id
    return row


class TestFunctionalAssetsApi:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from aite import config
        from aite.web.app import create_app

        scen_root = tmp_path / "scenarios"
        monkeypatch.setattr(config, "DEFAULT_FUNCTIONAL_SCENARIOS_DIR",
                            str(scen_root))
        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c, scen_root

    def _make_scenario(self, client, sid="s1", name="场景一"):
        resp = client.post("/api/v1/functional/scenarios",
                           json={"id": sid, "display_name": name})
        assert resp.status_code == 201
        return resp.json()

    # ------------------------------------------------------------- 场景
    def test_delete_scenario_removes_directory(self, client):
        """删除用户场景 = 整目录移除；列表消失；builtin/不存在走 409/404。"""
        c, scen_root = client
        self._make_scenario(c, sid="gone", name="待删场景")
        resp = c.delete("/api/v1/functional/scenarios/gone")
        assert resp.status_code == 200
        assert not (scen_root / "gone").exists()
        assert all(s["id"] != "gone" for s in
                   c.get("/api/v1/functional/scenarios").json()["items"])
        assert c.delete("/api/v1/functional/scenarios/gone").status_code == 404
        assert c.delete("/api/v1/functional/scenarios/basic_chat").status_code == 409

    def test_delete_scenario_cascades_scripts(self, client):
        """删场景连带脚本登记/行文件/派生 cases.jsonl（整目录语义）。"""
        c, scen_root = client
        self._make_scenario(c, sid="withscripts")
        body = {"scenario_id": "withscripts", "script_id": "sc_a", "name": "脚本A",
                "case_type": "基础", "rows": [_case("r1")]}
        assert c.post("/api/v1/functional/scripts", json=body).status_code == 201
        assert c.delete("/api/v1/functional/scenarios/withscripts").status_code == 200
        assert not (scen_root / "withscripts").exists()
        assert c.get("/api/v1/functional/scripts/sc_a").status_code == 404

    def test_import_overwrites_same_id(self, client):
        """同 id 重导 = 覆盖（同场景整体重写；跨场景迁移；新 id 标记 false）。"""
        c, scen_root = client
        self._make_scenario(c, sid="ow")
        jl1 = json.dumps({"desc": "d1", "messages": [{"role": "user", "content": "hi"}],
                          "expect": {"nonempty": True}}, ensure_ascii=False)
        body = {"scenario_id": "ow", "script_id": "ow_sc", "name": "V1",
                "case_type": "基础", "jsonl": jl1}
        assert c.post("/api/v1/functional/scripts/import", json=body).json()["overwritten"] is False
        # 同场景覆盖：行数与名称整体重写
        jl2 = jl1 + chr(10) + json.dumps({"desc": "d2", "messages": [{"role": "user", "content": "yo"}],
                                          "expect": {"nonempty": True}}, ensure_ascii=False)
        body2 = {**body, "name": "V2", "jsonl": jl2}
        r2 = c.post("/api/v1/functional/scripts/import", json=body2).json()
        assert r2["overwritten"] is True and r2["row_count"] == 2
        assert c.get("/api/v1/functional/scripts/ow_sc").json()["name"] == "V2"
        # 跨场景覆盖 = 迁移（旧场景删除，新场景创建）
        self._make_scenario(c, sid="ow2")
        body3 = {**body2, "scenario_id": "ow2"}
        r3 = c.post("/api/v1/functional/scripts/import", json=body3).json()
        assert r3["overwritten"] is True and r3["scenario"] == "ow2"
        assert c.get("/api/v1/functional/scripts/ow_sc").json()["scenario"] == "ow2"

    def test_script_list_has_row_index(self, client):
        """清单带 row_index（[id, desc]）——前端任务树用例数据源。"""
        c, _ = client
        self._make_scenario(c, sid="ri")
        body = {"scenario_id": "ri", "script_id": "ri_sc", "name": "RI",
                "case_type": "基础", "rows": [_case("r1"), _case("r2")]}
        assert c.post("/api/v1/functional/scripts", json=body).status_code == 201
        items = c.get("/api/v1/functional/scripts?scenario=ri").json()["items"]
        assert items[0]["row_index"] == [["r1", "用例 r1"], ["r2", "用例 r2"]]

    def test_scenarios_merge_builtin_and_user(self, client):
        c, _ = client
        items = c.get("/api/v1/functional/scenarios").json()["items"]
        ids = {i["id"]: i for i in items}
        assert ids["basic_chat"]["source"] == "builtin"
        assert all(i["display_name"] for i in items)

    def test_create_scenario_manifest_and_detail(self, client):
        c, root = client
        made = self._make_scenario(c)
        assert made["display_name"] == "场景一"
        assert json.loads((root / "s1" / "manifest.json").read_text(
            encoding="utf-8"))["dimension"] == ""
        dup = c.post("/api/v1/functional/scenarios", json={"id": "s1"})
        assert dup.status_code == 409
        builtin_dup = c.post("/api/v1/functional/scenarios",
                             json={"id": "basic_chat"})
        assert builtin_dup.status_code == 409
        bad = c.post("/api/v1/functional/scenarios", json={"id": "Bad Id"})
        assert bad.status_code == 422
        detail = c.get("/api/v1/functional/scenarios/s1").json()
        assert detail["script_entries"] == []

    def test_scenario_source_filter(self, client):
        c, _ = client
        self._make_scenario(c, sid="u1")
        items = c.get("/api/v1/functional/scenarios",
                      params={"source": "user"}).json()["items"]
        assert {i["id"] for i in items} == {"u1"}

    # ------------------------------------------------------------- 标签库
    def test_dim_tags_builtin_and_custom(self, client):
        c, _ = client
        items = c.get("/api/v1/functional/dim-tags").json()["items"]
        assert {"API 契约", "模型能力", "Agent"} <= {i["name"] for i in items}
        made = c.post("/api/v1/functional/dim-tags", json={"name": "安全合规"})
        assert made.status_code == 201
        dup = c.post("/api/v1/functional/dim-tags", json={"name": "安全合规"})
        assert dup.status_code == 409
        dup_builtin = c.post("/api/v1/functional/dim-tags",
                             json={"name": "API 契约"})
        assert dup_builtin.status_code == 409
        names = {i["name"]: i["source"]
                 for i in c.get("/api/v1/functional/dim-tags").json()["items"]}
        assert names["安全合规"] == "custom" and names["API 契约"] == "builtin"

    # ------------------------------------------------------------- 脚本 CRUD
    def test_script_create_materializes_cases_jsonl(self, client):
        c, root = client
        self._make_scenario(c)
        rows = [_case(content="a"), _case(content="b")]
        resp = c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s1", "script_id": "basic_two",
            "name": "基础 · 双变体", "case_type": "基础", "rows": rows})
        assert resp.status_code == 201
        body = resp.json()
        assert body["row_count"] == 2 and body["source"] == "manual"
        derived = (root / "s1" / "cases.jsonl").read_text(encoding="utf-8")
        derived_rows = [json.loads(line) for line in derived.splitlines()]
        assert [r["id"] for r in derived_rows] == ["basic_two.1", "basic_two.2"]
        assert "\r" not in derived                     # LF 强制（Windows 教训）

    def test_script_create_rejects_builtin_and_bad_rows(self, client):
        c, root = client
        readonly = c.post("/api/v1/functional/scripts", json={
            "scenario_id": "basic_chat", "script_id": "x1",
            "name": "n", "rows": [_case()]})
        assert readonly.status_code == 409
        self._make_scenario(c)
        bad = c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s1", "script_id": "x1", "name": "n",
            "rows": [{"desc": "坏断言", "messages": [], "expect": {"nope": 1}}]})
        assert bad.status_code == 422
        assert not (root / "s1" / "scripts.json").exists()   # 失败零落盘（原则 9）

    def test_script_row_id_conflict_across_scripts(self, client):
        c, _ = client
        self._make_scenario(c)
        ok = c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s1", "script_id": "alpha", "name": "A",
            "rows": [_case("fixed-id")]})
        assert ok.status_code == 201
        conflict = c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s1", "script_id": "beta", "name": "B",
            "rows": [_case("fixed-id")]})
        assert conflict.status_code == 422

    def test_script_get_patch_delete(self, client):
        c, root = client
        self._make_scenario(c)
        c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s1", "script_id": "p1", "name": "旧名",
            "rows": [_case()]})
        got = c.get("/api/v1/functional/scripts/p1").json()
        assert got["scenario"] == "s1" and len(got["rows"]) == 1
        patched = c.patch("/api/v1/functional/scripts/p1", json={
            "name": "新名", "case_type": "边界",
            "rows": [_case(), _case(content="b")]})
        assert patched.json()["name"] == "新名"
        assert patched.json()["case_type"] == "边界"
        lines = (root / "s1" / "cases.jsonl").read_text(
            encoding="utf-8").splitlines()
        assert len(lines) == 2                          # 派生随 rows 重写
        assert c.delete("/api/v1/functional/scripts/p1").json()["deleted"]
        assert not (root / "s1" / "cases.jsonl").read_text(encoding="utf-8")
        assert c.get("/api/v1/functional/scripts/p1").status_code == 404

    def test_script_list_across_scenarios(self, client):
        c, _ = client
        self._make_scenario(c, sid="s1")
        self._make_scenario(c, sid="s2", name="场景二")
        c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s1", "script_id": "in-s1", "name": "n",
            "rows": [_case()]})
        c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s2", "script_id": "in-s2", "name": "n",
            "rows": [_case()]})
        items = c.get("/api/v1/functional/scripts").json()["items"]
        assert {i["id"] for i in items} >= {"in-s1", "in-s2"}
        scoped = c.get("/api/v1/functional/scripts",
                       params={"scenario": "s2"}).json()["items"]
        assert len(scoped) == 1

    # ------------------------------------------------------------- legacy 收编
    def test_legacy_cases_adopted_on_first_write(self, client):
        c, root = client
        self._make_scenario(c)
        legacy = [_case("old-1"), _case("old-2")]
        (root / "s1" / "cases.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in legacy) + "\n",
            encoding="utf-8")
        resp = c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s1", "script_id": "new-1", "name": "新脚本",
            "rows": [_case("new-1")]})
        assert resp.status_code == 201
        derived = [json.loads(line) for line in
                   (root / "s1" / "cases.jsonl").read_text(
                       encoding="utf-8").splitlines()]
        assert [r["id"] for r in derived] == ["old-1", "old-2", "new-1"]
        legacy_entry = c.get("/api/v1/functional/scripts/legacy-cases").json()
        assert legacy_entry["case_type"] == "迁移"
        assert len(legacy_entry["rows"]) == 2

    # --------------------------------------------- 审视修复（2026-10-13）
    def test_corrupt_manifest_skipped_with_warning(self, client, caplog):
        """单个损坏 manifest 只跳过 + 告警，不拖垮整个场景列表（原则 9）。"""
        import logging
        c, root = client
        self._make_scenario(c, sid="good")
        c.post("/api/v1/functional/scenarios", json={"id": "bad"})
        (root / "bad" / "manifest.json").write_text("{corrupted", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            items = c.get("/api/v1/functional/scenarios").json()["items"]
        ids = {i["id"] for i in items}
        assert "good" in ids and "bad" not in ids      # 损坏目录被跳过
        assert any("跳过损坏的场景目录" in r.message for r in caplog.records)

    def test_corrupt_scripts_registry_skipped_in_list(self, client):
        c, root = client
        self._make_scenario(c, sid="okscen")
        c.post("/api/v1/functional/scripts", json={
            "scenario_id": "okscen", "script_id": "keep-me", "name": "n",
            "rows": [_case()]})
        c.post("/api/v1/functional/scenarios", json={"id": "brokenscen"})
        c.post("/api/v1/functional/scripts", json={
            "scenario_id": "brokenscen", "script_id": "broken-one",
            "name": "n", "rows": [_case()]})
        (root / "brokenscen" / "scripts.json").write_text("{bad", encoding="utf-8")
        items = c.get("/api/v1/functional/scripts").json()["items"]
        ids = {i["id"] for i in items}
        assert "keep-me" in ids                     # 用户自建脚本在列表中
        assert not any(i.get("readonly") for i in items if i["id"] == "keep-me")

    def test_legacy_bad_row_blocks_adoption(self, client):
        """既有 cases.jsonl 坏行 → 收编显式报错（原则 9），不静默入派生。"""
        c, root = client
        self._make_scenario(c)
        (root / "s1" / "cases.jsonl").write_text(
            json.dumps({"id": "bad-row", "desc": "坏断言",
                        "messages": [], "expect": {"nope": 1}},
                       ensure_ascii=False) + "\n", encoding="utf-8")
        resp = c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s1", "script_id": "new-1", "name": "新脚本",
            "rows": [_case("new-1")]})
        assert resp.status_code == 422
        assert "无法收编" in resp.json()["detail"]

    def test_create_script_case_type_whitelist(self, client):
        c, _ = client
        self._make_scenario(c)
        resp = c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s1", "script_id": "wt", "name": "n",
            "case_type": "乱写", "rows": [_case()]})
        assert resp.status_code == 422
        assert "非法用例类型" in resp.json()["detail"]

    def test_update_script_duplicate_id_in_payload(self, client):
        c, _ = client
        self._make_scenario(c)
        c.post("/api/v1/functional/scripts", json={
            "scenario_id": "s1", "script_id": "p9", "name": "n",
            "rows": [_case()]})
        resp = c.patch("/api/v1/functional/scripts/p9", json={
            "rows": [_case("same"), _case("same")]})
        assert resp.status_code == 422
        assert "场景内冲突" in resp.json()["detail"]

    def test_create_scenario_unknown_dimension(self, client):
        c, _ = client
        resp = c.post("/api/v1/functional/scenarios", json={
            "id": "dimbad", "display_name": "x", "dimension": "不存在的标签"})
        assert resp.status_code == 422
        assert "未知维度标签" in resp.json()["detail"]

    def test_import_bad_jsonl_reports_lineno(self, client):
        c, _ = client
        self._make_scenario(c)
        resp = c.post("/api/v1/functional/scripts/import", json={
            "scenario_id": "s1", "script_id": "bad-import", "name": "n",
            "jsonl": '{broken json'})
        assert resp.status_code == 422
        assert "第 1 行" in resp.json()["detail"]

    def test_dim_tag_integrity_race_maps_409(self, client):
        """并发双击：唯一约束 IntegrityError → FileExistsError（409 语义）。"""
        import asyncio

        from sqlalchemy.exc import IntegrityError

        from aite.service import functional_assets as assets

        class RaceRepo:
            async def list_dim_tags(self):
                return []

            async def create_dim_tag(self, name):
                raise IntegrityError("stmt", {}, Exception("duplicate"))

        async def main():
            return await assets.create_dim_tag(RaceRepo(), "并发标签")

        try:
            asyncio.run(main())
        except FileExistsError:
            pass
        else:
            raise AssertionError("IntegrityError 未映射为 FileExistsError")

    # ------------------------------------------------------------- 导入
    def test_import_script_from_jsonl_and_dataset(self, client):
        c, _ = client
        self._make_scenario(c)
        both = c.post("/api/v1/functional/scripts/import", json={
            "scenario_id": "s1", "script_id": "i1", "name": "n",
            "jsonl": "x", "dataset": "y"})
        assert both.status_code == 422
        neither = c.post("/api/v1/functional/scripts/import", json={
            "scenario_id": "s1", "script_id": "i1", "name": "n"})
        assert neither.status_code == 422
        pasted = c.post("/api/v1/functional/scripts/import", json={
            "scenario_id": "s1", "script_id": "i1", "name": "粘贴导入",
            "case_type": "边界",
            "jsonl": json.dumps(_case("g-01"), ensure_ascii=False)})
        assert pasted.status_code == 201
        assert pasted.json()["source"] == "import"
        seeded = c.post("/api/v1/datasets/functional/import", json={
            "name": "mini-set", "notes": "",
            "jsonl": "\n".join(json.dumps(_case(f"d-{i}"), ensure_ascii=False)
                               for i in (1, 2))})
        assert seeded.status_code == 201
        from_ds = c.post("/api/v1/functional/scripts/import", json={
            "scenario_id": "s1", "script_id": "i2", "name": "数据集导入",
            "dataset": "mini-set"})
        assert from_ds.status_code == 201
        assert from_ds.json()["row_count"] == 2
