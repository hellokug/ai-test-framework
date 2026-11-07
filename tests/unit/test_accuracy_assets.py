"""精度域脚本资产层单元测试（builtin 扫描 / 自定义 CRUD / 真实校验）。"""

import pytest

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


@pytest.fixture()
def custom_root(tmp_path, monkeypatch):
    from aite import config

    root = tmp_path / "acc-scripts"
    monkeypatch.setattr(config, "DEFAULT_ACCURACY_SCRIPTS_DIR", str(root))
    return root


class TestBuiltinScan:
    def test_builtin_scripts_listed(self):
        """预置 = examples/accuracy/*.yaml 全量包装为 doc（id=文件名，source=builtin）。"""
        out = assets.list_scripts()
        ids = {i["id"] for i in out["items"]}
        assert {"ceval", "gsm8k", "agent_eval", "swebench"} <= ids
        assert out["count"] == len(out["items"])
        agent = assets.get_script("agent_eval")
        assert agent["source"] == "builtin"
        assert agent["spec"]["target"]["type"] == "custom_http"
        assert agent["name"]   # description 首行 / run_name 回退

    def test_source_filter(self):
        only_custom = assets.list_scripts(source="custom")
        assert all(i["source"] == "custom" for i in only_custom["items"])

    def test_get_missing_raises(self):
        with pytest.raises(FileNotFoundError):
            assets.get_script("no-such-script")


class TestCustomCrud:
    def test_save_get_update_delete_roundtrip(self, custom_root):
        doc = assets.save_script("my-baseline", "我的基线", "说明", _spec())
        assert doc["source"] == "custom"
        assert doc["created_at"] and doc["updated_at"]
        got = assets.get_script("my-baseline")
        assert got["name"] == "我的基线"
        assert got["spec"]["tools"]["datasets"] == ["gsm8k"]
        # 文件强制 LF（Windows 宿主 CRLF 教训）
        raw = (custom_root / "my-baseline.yaml").read_bytes()
        assert b"\r" not in raw

        assets.update_script("my-baseline", name="改名")
        assert assets.get_script("my-baseline")["name"] == "改名"
        assets.update_script("my-baseline", spec=_spec(run_name="v2"))
        assert assets.get_script("my-baseline")["spec"]["run_name"] == "v2"

        assets.delete_script("my-baseline")
        with pytest.raises(FileNotFoundError):
            assets.get_script("my-baseline")

    def test_save_rejects_bad_id(self, custom_root):
        with pytest.raises(ValueError, match="id 不合法"):
            assets.save_script("Bad ID", "x", "", _spec())

    def test_save_rejects_duplicate_and_builtin_collision(self, custom_root):
        assets.save_script("dup", "x", "", _spec())
        with pytest.raises(FileExistsError):
            assets.save_script("dup", "x", "", _spec())
        with pytest.raises(FileExistsError, match="预置"):
            assets.save_script("ceval", "x", "", _spec())

    def test_update_builtin_readonly(self, custom_root):
        with pytest.raises(PermissionError):
            assets.update_script("ceval", name="改预置")
        with pytest.raises(PermissionError):
            assets.delete_script("ceval")

    def test_save_rejects_invalid_spec(self, custom_root):
        with pytest.raises(ValueError, match="数据集"):
            assets.save_script("bad", "x", "", {"module": "accuracy"})
        # 自研域强校验：extra=forbid 不认未知键（fail-fast 显式报错）
        bad = {k: v for k, v in _spec().items() if k != "engine"}
        bad["bogus_key"] = 1
        with pytest.raises(ValueError, match="校验失败"):
            assets.save_script("bad2", "x", "", bad)

    def test_save_accepts_selfbuilt_spec(self, custom_root):
        """自研引擎形态（无 engine 字段，如 agent_eval）同样过强校验。"""
        spec = {
            "module": "accuracy", "run_name": "agent-mini",
            "target": {"type": "custom_http", "base_url": "http://x", "extract": "output"},
            "datasets": [{"type": "local", "path": "d.jsonl",
                          "fields": {"input": "q", "expected": "a"}}],
            "metrics": [{"contains": {}}],
        }
        doc = assets.save_script("agent-mini", "Agent 迷你", "", spec)
        assert doc["spec"]["run_name"] == "agent-mini"

    def test_corrupted_custom_file_skipped_in_list(self, custom_root):
        assets.save_script("good", "x", "", _spec())
        (custom_root / "broken.yaml").write_text("{ not: valid: yaml", encoding="utf-8")
        ids = {i["id"] for i in assets.list_scripts()["items"]}
        assert "good" in ids and "broken" not in ids
