"""精度域场景/脚本资产层单元测试（无预置 / 场景与脚本 CRUD / 条数口径）。"""

import pytest
import yaml

from aite.service import accuracy_assets as assets


def _spec(run_name="my-baseline"):
    """最小合法精度 spec（工具域 evalscope 形态，规则判分零成本）。"""
    return {
        "module": "accuracy",
        "run_name": run_name,
        "engine": "evalscope",
        "target": {"profile": "deepseek"},
        "tools": {"datasets": ["gsm8k"], "limit_per_subset": 5},
        "report": ["json"],
        "output_dir": "./reports",
        "storage": {"url": "sqlite+aiosqlite:///./aite.db"},
    }


def _selfbuilt_spec(run_name="agent-mini"):
    """自研引擎形态（无 engine 字段；datasets 走 builtin 打包样本）。"""
    return {
        "module": "accuracy", "run_name": run_name,
        "target": {"type": "custom_http", "base_url": "http://x", "extract": "output"},
        "datasets": [{"type": "builtin", "id": "gsm8k"}],
        "metrics": [{"contains": {}}],
    }


class FakeRepo:
    """catalog 内存桩：resolve_dataset_ref 需要的三个读口（原则 9 显式解析）。"""

    def __init__(self, rows=None, tags=None):
        self.rows = rows or {}
        self.tags = tags or ["API 契约", "模型能力", "Agent"]

    async def get_dataset_catalog(self, ref):
        return self.rows.get(ref)

    async def find_dataset_catalog(self, type_part, name_part):
        return self.rows.get(f"{type_part}:{name_part}")

    async def list_dataset_catalog(self, limit=1000):
        return [{"name": name, **row} for name, row in self.rows.items()]

    async def list_dim_tags(self):
        return [{"name": t, "id": None, "created_at": ""} for t in self.tags]


@pytest.fixture()
def scen_root(tmp_path, monkeypatch):
    from aite import config

    root = tmp_path / "acc-scen"
    monkeypatch.setattr(config, "DEFAULT_ACCURACY_SCENARIOS_DIR", str(root))
    return root


async def _mk_scenario(repo, sid="s1", name="场景一", dimension=""):
    return await assets.create_scenario(repo, sid, name, dimension, "")


class TestNoPreset:
    @pytest.mark.asyncio
    async def test_empty_root_lists_nothing(self, scen_root):
        """场景与脚本全部用户自管：空根 → 空清单（无 builtin 扫描）。"""
        assert await assets.scan_scenarios(FakeRepo()) == []
        out = await assets.list_scripts(FakeRepo())
        assert out == []


class TestScenarioCrud:
    @pytest.mark.asyncio
    async def test_create_manifest_and_summary(self, scen_root):
        repo = FakeRepo()
        row = await _mk_scenario(repo, dimension="Agent")
        assert row == {"id": "s1", "display_name": "场景一", "dimension": "Agent",
                       "description": "", "scripts": 0, "cases": 0}
        import json

        manifest = json.loads((scen_root / "s1" / "manifest.json").read_text(
            encoding="utf-8"))
        assert manifest["display_name"] == "场景一"
        assert manifest["dimension"] == "Agent"

    @pytest.mark.asyncio
    async def test_create_rejects_bad_id_and_unknown_dimension(self, scen_root):
        repo = FakeRepo()
        await _mk_scenario(repo)
        with pytest.raises(FileExistsError):
            await _mk_scenario(repo)
        with pytest.raises(ValueError, match="英文编号"):
            await assets.create_scenario(repo, "Bad Id")
        with pytest.raises(ValueError, match="未知维度标签"):
            await assets.create_scenario(repo, "ok", dimension="不存在的标签")

    @pytest.mark.asyncio
    async def test_delete_gated_then_force_cascade(self, scen_root):
        repo = FakeRepo()
        await _mk_scenario(repo, sid="gone")
        await assets.create_script(repo, scenario_id="gone", script_id="sc_a",
                                   name="脚本A", spec=_spec())
        with pytest.raises(assets.ScenarioNotEmptyError, match="仍有 1 个脚本"):
            await assets.delete_scenario("gone")
        row = await assets.delete_scenario("gone", force=True)
        assert row["scripts"] == 1
        assert not (scen_root / "gone").exists()
        with pytest.raises(FileNotFoundError):
            await assets.delete_scenario("gone")

    @pytest.mark.asyncio
    async def test_empty_scenario_deletes_directly(self, scen_root):
        repo = FakeRepo()
        await _mk_scenario(repo, sid="empty")
        assert (await assets.delete_scenario("empty"))["id"] == "empty"

    @pytest.mark.asyncio
    async def test_summary_cases_sum_and_unknown_none(self, scen_root):
        """场景条数 = 各脚本条数合计；任一脚本不可落定 → None（不虚报）。"""
        from aite.datasets import builtin

        repo = FakeRepo(rows={"fin": {"path": "x.jsonl", "stats": {"count": 12}}})
        await _mk_scenario(repo, sid="mix")
        await assets.create_script(repo, scenario_id="mix", script_id="local",
                                   name="自研", spec=_selfbuilt_spec())
        await assets.create_script(repo, scenario_id="mix", script_id="tool",
                                   name="工具域", spec=_spec())
        row = await assets.scenario_summary(repo, "mix")
        assert row["scripts"] == 2 and row["cases"] is None   # 工具域条数不可知
        await assets.delete_script("tool")
        expected = len(builtin._read_entry(
            builtin.merged_info()["gsm8k"]))
        assert (await assets.scenario_summary(repo, "mix"))["cases"] == expected


class TestScriptCrud:
    @pytest.mark.asyncio
    async def test_create_get_update_delete_roundtrip(self, scen_root):
        repo = FakeRepo()
        await _mk_scenario(repo)
        doc = await assets.create_script(repo, scenario_id="s1",
                                         script_id="my-baseline", name="我的基线",
                                         description="版本基线", spec=_spec())
        assert doc["name"] == "我的基线" and doc["scenario"] == "s1"
        assert doc["description"] == "版本基线"
        assert doc["created_at"] and doc["updated_at"]
        # 文件强制 LF（Windows 宿主 CRLF 教训）；doc 落盘含 spec
        raw = (scen_root / "s1" / "scripts" / "my-baseline.yaml").read_bytes()
        assert b"\r" not in raw
        on_disk = yaml.safe_load(raw.decode("utf-8"))
        assert on_disk["spec"]["tools"]["datasets"] == ["gsm8k"]

        await assets.update_script(repo, "my-baseline", name="改名")
        assert (await assets.get_script(repo, "my-baseline"))["name"] == "改名"
        await assets.update_script(repo, "my-baseline",
                                   spec=_spec(run_name="v2"))
        got = await assets.get_script(repo, "my-baseline")
        assert got["spec"]["run_name"] == "v2"
        assert got["datasets"] == ["gsm8k"]

        row = await assets.delete_script("my-baseline")
        assert row["deleted"] and row["scenario"] == "s1"
        with pytest.raises(FileNotFoundError):
            await assets.get_script(repo, "my-baseline")

    @pytest.mark.asyncio
    async def test_list_is_slim_no_description_no_spec(self, scen_root):
        """清单瘦身口径：数据集 + 条数 + 更新时间，不带描述/spec。"""
        repo = FakeRepo()
        await _mk_scenario(repo)
        await assets.create_script(repo, scenario_id="s1", script_id="slim",
                                   name="瘦身", description="不该出现在清单",
                                   spec=_spec())
        items = await assets.list_scripts(repo)
        item = items[0]
        assert item["id"] == "slim" and item["name"] == "瘦身"
        assert item["scenario"] == "s1" and item["scenario_name"] == "场景一"
        assert item["datasets"] == ["gsm8k"] and item["case_count"] is None
        assert "description" not in item and "spec" not in item
        scoped = await assets.list_scripts(repo, scenario_id="other")
        assert scoped == []

    @pytest.mark.asyncio
    async def test_script_id_unique_across_scenarios(self, scen_root):
        """脚本 id 全平台唯一：跨场景同 id → 409 语义。"""
        repo = FakeRepo()
        await _mk_scenario(repo, sid="s1")
        await _mk_scenario(repo, sid="s2", name="场景二")
        await assets.create_script(repo, scenario_id="s1", script_id="dup",
                                   name="A", spec=_spec())
        with pytest.raises(FileExistsError, match="全平台唯一"):
            await assets.create_script(repo, scenario_id="s2", script_id="dup",
                                       name="B", spec=_spec())

    @pytest.mark.asyncio
    async def test_create_rejects_missing_scenario_and_bad_id(self, scen_root):
        repo = FakeRepo()
        with pytest.raises(FileNotFoundError):
            await assets.create_script(repo, scenario_id="no-such",
                                       script_id="x1", name="n", spec=_spec())
        await _mk_scenario(repo)
        with pytest.raises(ValueError, match="英文编号"):
            await assets.create_script(repo, scenario_id="s1", script_id="Bad Id",
                                       name="n", spec=_spec())

    @pytest.mark.asyncio
    async def test_save_rejects_invalid_spec(self, scen_root):
        repo = FakeRepo()
        await _mk_scenario(repo)
        with pytest.raises(ValueError, match="数据集"):
            await assets.create_script(repo, scenario_id="s1", script_id="bad",
                                       name="x", spec={"module": "accuracy"})
        # 自研域强校验：extra=forbid 不认未知键（fail-fast 显式报错）
        bad = _selfbuilt_spec()
        bad["bogus_key"] = 1
        with pytest.raises(ValueError, match="校验失败"):
            await assets.create_script(repo, scenario_id="s1", script_id="bad2",
                                       name="x", spec=bad)

    @pytest.mark.asyncio
    async def test_saved_spec_keeps_profile_reference(self, scen_root, monkeypatch):
        """校验不改原件：common 预制展开（profile → 端点、工具缺省 python_exe）
        只发生在校验副本上，落盘 spec 保留 profile 引用、不泄漏本机路径。"""
        from aite.core import profiles

        common = {"models": {"deepseek": {"type": "openai_compatible",
                                          "base_url": "https://api.deepseek.com/v1",
                                          "model": "deepseek-chat"}},
                  "tools": {"extra_args": {"python_exe": "D:/envs/evalscope/python.exe"}}}
        monkeypatch.setattr(profiles, "load_common", lambda: common)
        repo = FakeRepo()
        await _mk_scenario(repo)
        await assets.create_script(repo, scenario_id="s1", script_id="prof",
                                   name="预制引用", spec=_spec())
        on_disk = yaml.safe_load((scen_root / "s1" / "scripts" / "prof.yaml")
                                 .read_text(encoding="utf-8"))
        assert on_disk["spec"]["target"] == {"profile": "deepseek"}
        assert "extra_args" not in on_disk["spec"]["tools"]

    @pytest.mark.asyncio
    async def test_corrupted_script_file_skipped(self, scen_root):
        """单个损坏脚本跳过 + 告警，不推翻清单（原则 9）。"""
        repo = FakeRepo()
        await _mk_scenario(repo)
        await assets.create_script(repo, scenario_id="s1", script_id="good",
                                   name="好脚本", spec=_spec())
        (scen_root / "s1" / "scripts" / "broken.yaml").write_text(
            "{ not: valid: yaml", encoding="utf-8")
        ids = {i["id"] for i in await assets.list_scripts(repo)}
        assert ids == {"good"}


class TestCaseCounting:
    @pytest.mark.asyncio
    async def test_selfbuilt_builtin_packaged_count(self, scen_root):
        """自研域 builtin 引用 = 打包样本真实行数。"""
        from aite.datasets import builtin

        repo = FakeRepo()
        await _mk_scenario(repo)
        doc = await assets.create_script(repo, scenario_id="s1",
                                         script_id="pack", name="打包",
                                         spec=_selfbuilt_spec())
        expected = len(builtin._read_entry(builtin.merged_info()["gsm8k"]))
        assert doc["case_count"] == expected > 0

    @pytest.mark.asyncio
    async def test_catalog_count_and_local_path_count(self, scen_root, tmp_path):
        repo = FakeRepo(rows={
            "fin": {"path": str(tmp_path / "fin.jsonl"), "stats": {"count": 12}}})
        await _mk_scenario(repo)
        spec = _selfbuilt_spec()
        spec["datasets"] = [{"type": "ref", "ref": "fin"},
                            {"type": "local", "path": str(tmp_path / "loc.jsonl")}]
        (tmp_path / "loc.jsonl").write_text('{"a":1}\n\n{"a":2}\n', encoding="utf-8")
        doc = await assets.create_script(repo, scenario_id="s1",
                                         script_id="cat", name="目录",
                                         spec=spec)
        assert doc["datasets"] == ["fin", str(tmp_path / "loc.jsonl")]
        assert doc["case_count"] == 14            # 12（catalog stats）+ 2（文件行数）

    @pytest.mark.asyncio
    async def test_tool_domain_count_unknown_without_catalog(self, scen_root):
        """工具域基准全量在 evalscope 侧：catalog 未登记 → 条数 None。"""
        repo = FakeRepo()
        await _mk_scenario(repo)
        doc = await assets.create_script(repo, scenario_id="s1",
                                         script_id="tool", name="工具域",
                                         spec=_spec())
        assert doc["case_count"] is None and doc["datasets"] == ["gsm8k"]

    @pytest.mark.asyncio
    async def test_tool_domain_count_via_catalog_registration(self, scen_root):
        """工具域引用若在 catalog 显式登记（add-hf 导入）→ 按登记条数。"""
        repo = FakeRepo(rows={"gsm8k": {"path": "p.jsonl", "stats": {"count": 5}}})
        await _mk_scenario(repo)
        doc = await assets.create_script(repo, scenario_id="s1",
                                         script_id="tool2", name="工具域2",
                                         spec=_spec())
        assert doc["case_count"] == 5

    @pytest.mark.asyncio
    async def test_partial_known_refs_yield_none(self, scen_root):
        """混合引用：一个不可落定 → 整体 None（不虚报部分和）。"""
        repo = FakeRepo()
        await _mk_scenario(repo)
        spec = _selfbuilt_spec()
        spec["datasets"] = [{"type": "builtin", "id": "gsm8k"},
                            {"type": "builtin", "id": "no-such-dataset"}]
        doc = await assets.create_script(repo, scenario_id="s1",
                                         script_id="mix", name="混合",
                                         spec=spec)
        assert doc["case_count"] is None
