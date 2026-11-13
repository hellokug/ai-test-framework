"""harness.Case 校验器防线测试（内置场景已移除，数据集用例由用户自建）。"""

import pytest

from aite.functional.harness import Case


@pytest.mark.parametrize("bad", [
    {"expect": {"unknown_key": 1}},
    {"expect": {"finish_reason": "bogus"}},
])
def test_invalid_expect_still_rejected(bad):
    """校验器防线：非法断言键/值构造 Case 必须抛错（真实校验器在位）。"""
    raw = {"id": "x", "desc": "d", "messages": [{"role": "user", "content": "hi"}],
           "tools": [], **bad}
    with pytest.raises(ValueError):
        Case(raw, "invalid-test")
