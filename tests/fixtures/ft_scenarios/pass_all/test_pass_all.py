"""离线端到端 fixture：全通过场景（不依赖模型，纯断言）。"""


def test_pass_one():
    assert 1 + 1 == 2


def test_pass_two():
    assert "a" * 3 == "aaa"
