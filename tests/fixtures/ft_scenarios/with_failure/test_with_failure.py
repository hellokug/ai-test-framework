"""离线端到端 fixture：含失败用例的场景（不依赖模型，纯断言）。"""


def test_pass():
    assert True


def test_fail():
    assert 1 == 2, "确定性失败（fixture 用例）"
