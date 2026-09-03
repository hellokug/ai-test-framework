"""场景：基础对话与指令遵循（4 例）。

抗 flaky 原则：约束式 prompt + 确定性目标断言（宽松格式，不要求逐字匹配）。
"""

def test_reply_nonempty(chat):
    reply = chat([{"role": "user", "content": "用一句话介绍你自己。"}])
    assert reply.strip()


def test_keyword_inclusion(chat):
    reply = chat([{
        "role": "user",
        "content": "Reply with one short English sentence that contains the word 'banana'.",
    }])
    assert "banana" in reply.lower()


def test_language_and_format_constraint(chat):
    reply = chat([{
        "role": "user",
        "content": "Answer with exactly one English word, yes or no: Is Paris the capital of France?",
    }])
    assert "yes" in reply.lower()


def test_simple_arithmetic(chat):
    reply = chat([{"role": "user", "content": "只输出一个数字，不要任何其他文字：3+4=?"}])
    assert "7" in reply
