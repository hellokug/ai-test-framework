"""agent_tasks 场景数据集测试（模块：精度/功能侧 agent 评测集加载）。"""

import json
from pathlib import Path

import pytest

from aite.functional.harness import Case

SCENARIOS = Path(__file__).parents[2] / "aite" / "functional" / "scenarios"


class TestAgentTasksDataset:
    def test_dataset_loads_via_real_validator(self):
        """每条用例须经 harness.Case 真实校验器构造（schema/断言键全合法）。"""
        path = SCENARIOS / "agent_tasks" / "cases.jsonl"
        cases = [Case(json.loads(line), "agent_tasks")
                 for line in path.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        assert len(cases) >= 10

    def test_dataset_covers_agent_capability_spectrum(self):
        """覆盖面：工具选择 / 参数抽取 / 拒答（no_tool）/ 多轮续作。"""
        path = SCENARIOS / "agent_tasks" / "cases.jsonl"
        cases = [json.loads(line) for line in
                 path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert any("no_tool" in c["expect"] for c in cases)          # 拒答类
        assert any("tool_args_contains" in c["expect"] for c in cases)  # 参数抽取
        assert any("finish_reason" in c["expect"] for c in cases)    # 结构域
        assert sum(1 for c in cases if len(c["messages"]) > 1) >= 2  # 多轮
        assert all(c["tools"] for c in cases)
        assert len({c["expect"].get("tool") for c in cases}) >= 5    # 工具多样性

    def test_case_ids_unique(self):
        path = SCENARIOS / "agent_tasks" / "cases.jsonl"
        ids = [json.loads(line)["id"] for line in
               path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(ids) == len(set(ids))

    def test_scenario_discoverable_by_engine(self):
        """引擎场景发现：agent_tasks 出现在内置场景清单（pytest 适配器同源）。"""
        from aite.functional import SCENARIOS_DIR

        assert (SCENARIOS_DIR / "agent_tasks" / "cases.jsonl").is_file()

    @pytest.mark.parametrize("bad", [
        {"expect": {"unknown_key": 1}},
        {"expect": {"finish_reason": "bogus"}},
    ])
    def test_invalid_expect_still_rejected(self, bad):
        """校验器防线：非法断言键/值构造 Case 必须抛错（真实校验器在位）。"""
        raw = {"id": "x", "desc": "d", "messages": [{"role": "user", "content": "hi"}],
               "tools": [], **bad}
        with pytest.raises(ValueError):
            Case(raw, "invalid-test")
