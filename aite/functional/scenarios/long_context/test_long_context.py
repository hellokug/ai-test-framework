"""场景：长上下文（3 例）：needle-in-a-haystack。

抗 flaky 原则：确定性填充文本（重复无意义句，无随机）、自造密钥词，
断言密钥词出现在回复中。
"""

KEY = "ITE-7Q4X"
FILLER = "The quiet train crosses the long bridge every morning before sunrise.\n"


def build_doc(total_chars: int, key_at: float) -> str:
    """构造 total_chars 量级的长文档，密钥句埋在 key_at 比例处。"""
    doc = FILLER * (total_chars // len(FILLER))
    idx = int(len(doc) * key_at)
    return doc[:idx] + f"\nThe access code is {KEY}.\n" + doc[idx:]


def ask_code(chat, doc: str) -> str:
    return chat([
        {"role": "system", "content": "Read the document and answer the question."},
        {"role": "user", "content": f"Document:\n{doc}\n\n"
                                    "Question: What is the access code mentioned in the "
                                    "document? Reply with only the code itself."},
    ])


def test_needle_in_middle(chat):
    assert KEY in ask_code(chat, build_doc(4000, 0.5))


def test_needle_in_long_16k(chat):
    assert KEY in ask_code(chat, build_doc(16000, 0.5))


def test_needle_at_tail(chat):
    assert KEY in ask_code(chat, build_doc(4000, 0.9))
