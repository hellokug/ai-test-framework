"""场景：结构化输出（4 例）：可解析 + 字段类型 / 长度 / 枚举 / 嵌套正确。"""

import json

from aite.functional.harness import strip_fences


def _json(chat, prompt) -> dict | list:
    reply = chat([{"role": "user", "content": prompt}])
    return json.loads(strip_fences(reply))


def test_flat_object_types(chat):
    data = _json(chat, 'Return ONLY a JSON object with keys "name" (string) and '
                       '"age" (integer), for a person named Alice who is 30 years old. '
                       "No other text.")
    assert isinstance(data.get("name"), str) and data["name"]
    assert isinstance(data.get("age"), int) and not isinstance(data.get("age"), bool)


def test_fixed_length_array(chat):
    data = _json(chat, "Return ONLY a JSON array containing exactly 3 color names as "
                       "strings. No other text.")
    assert isinstance(data, list) and len(data) == 3
    assert all(isinstance(x, str) and x for x in data)


def test_enum_field(chat):
    data = _json(chat, 'Classify the sentiment of the text as one of "positive", '
                       '"negative", "neutral". Return ONLY a JSON object: '
                       '{"sentiment": "<value>"}. '
                       "Text: 这是我用过的最好的产品，体验非常满意！")
    assert data.get("sentiment") in {"positive", "negative", "neutral"}
    assert data["sentiment"] == "positive"   # 强情感句，确定性目标


def test_nested_object(chat):
    data = _json(chat, 'Return ONLY a JSON object {"user": {"name": "<string>", '
                       '"address": {"city": "<string>"}}} with name "Alice" and '
                       'city "Shanghai". No other text.')
    assert data["user"]["name"].lower() == "alice"
    assert data["user"]["address"]["city"].lower() == "shanghai"
