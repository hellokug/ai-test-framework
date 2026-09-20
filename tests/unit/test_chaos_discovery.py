"""发现层测试（节点>容器>进程 模糊查询树视图）+ 注入武器目录。"""

import os
import socket

from aite.chaos.discovery import discover_targets
from aite.chaos.injectors.base import list_weapons


class TestDiscovery:
    def test_full_tree_shape(self):
        tree = discover_targets()
        assert tree["node"]["name"] == socket.gethostname()
        assert tree["matched"] is True
        assert isinstance(tree["containers"], list)
        assert isinstance(tree["processes"], list)
        assert tree["counts"]["processes"] == len(tree["processes"])

    def test_process_fuzzy_match_hits_self(self):
        """模糊匹配：本测试进程（python）必在结果中，且 cmdline 也可命中。"""
        tree = discover_targets({"process": "python"})
        pids = [p["pid"] for p in tree["processes"]]
        assert os.getpid() in pids or any("python" in p["name"].lower()
                                          for p in tree["processes"])
        assert all("python" in (p["name"] + p["cmdline"]).lower()
                   for p in tree["processes"])

    def test_process_fuzzy_no_match(self):
        tree = discover_targets({"process": "zz-definitely-missing-xyz"})
        assert tree["processes"] == []
        assert tree["counts"]["processes"] == 0

    def test_node_filter_mismatch_empties_view(self):
        tree = discover_targets({"node": "no-such-node-xyz"})
        assert tree["matched"] is False
        assert tree["processes"] == [] and tree["containers"] == []

    def test_container_layer_degrades_gracefully(self):
        """docker 缺失/可用都不炸：容器层要么有清单，要么为空（可用性优先）。"""
        tree = discover_targets({"container": "whatever"})
        assert isinstance(tree["containers"], list)
        assert isinstance(tree["notes"], list)

    def test_container_layer_notes_when_docker_missing(self, monkeypatch):
        """docker CLI 不可用 → 容器层空 + note 显式说明（不抛错）。"""
        from aite.chaos import discovery

        monkeypatch.setattr(discovery, "_docker", lambda args: None)
        tree = discovery.discover_targets({"container": "x"})
        assert tree["containers"] == []
        assert any("docker" in n for n in tree["notes"])


class TestWeapons:
    def test_weapon_catalog(self):
        weapons = {w["injector"]: w["kinds"] for w in list_weapons()}
        assert "process" in weapons
        assert set(weapons["process"]) == {"kill", "suspend"}
        if "toxiproxy" in weapons:      # toxiproxy 模块导入即注册
            assert set(weapons["toxiproxy"]) == {"latency", "timeout", "bandwidth"}

    def test_weapon_catalog_schemas_self_describing(self):
        """逐 kind 参数 schema（§14.2）：toxiproxy latency 必填 latency_ms。"""
        weapons = {w["injector"]: w for w in list_weapons()}
        schema = weapons["toxiproxy"]["schemas"]["latency"]
        assert schema["latency_ms"]["required"] is True
        assert schema["latency_ms"]["type"] == "int"
        assert weapons["process"]["schemas"]["suspend"] == {}
