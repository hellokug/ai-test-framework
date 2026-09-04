"""cases.jsonl 数据驱动核心单测（加载校验 / expect DSL / vars 渲染，全离线）。"""

import json

import pytest

from aite.functional.harness import Case, build_haystack, load_cases


def _response(content="", tool_calls=None):
    message = {"content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def _tool_call(name="get_weather", arguments='{"city": "Tokyo"}'):
    return [{"function": {"name": name, "arguments": arguments}}]


def _case(expect, **kw):
    raw = {"id": "t", "desc": "测试", "messages": [{"role": "user", "content": "hi"}],
           "expect": expect}
    raw.update(kw)
    return Case(raw, "cases.jsonl:1")


def _write(tmp_path, lines, name="cases.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else x
                           for x in lines) + "\n", encoding="utf-8")
    return p


class TestLoadCases:
    def test_valid_lines_and_blank_skipped(self, tmp_path):
        p = _write(tmp_path, [
            {"id": "a", "desc": "非空", "messages": [{"role": "user", "content": "hi"}], "expect": {"nonempty": True}},
            "",
            {"id": "b", "desc": "包含", "messages": [{"role": "user", "content": "hi"}], "expect": {"contains": "x"}},
        ])
        cases = load_cases(p)
        assert [c.id for c in cases] == ["a", "b"]

    def test_missing_required_field(self, tmp_path):
        p = _write(tmp_path, [{"id": "a", "messages": [{"role": "user", "content": "hi"}]}])
        with pytest.raises(ValueError, match="必填字段"):
            load_cases(p)

    def test_duplicate_id(self, tmp_path):
        line = {"id": "a", "desc": "d", "messages": [{"role": "user", "content": "hi"}], "expect": {"nonempty": True}}
        p = _write(tmp_path, [line, line])
        with pytest.raises(ValueError, match="重复"):
            load_cases(p)

    def test_bad_json_line(self, tmp_path):
        p = _write(tmp_path, ["{not json"])
        with pytest.raises(ValueError, match="合法 JSON"):
            load_cases(p)

    def test_unknown_expect_key(self, tmp_path):
        p = _write(tmp_path, [{"id": "a", "desc": "d", "messages": [{"role": "user", "content": "hi"}],
                               "expect": {"contains_": "x"}}])
        with pytest.raises(ValueError, match="未知断言键"):
            load_cases(p)

    def test_unknown_json_spec_key(self, tmp_path):
        p = _write(tmp_path, [{"id": "a", "desc": "d", "messages": [{"role": "user", "content": "hi"}],
                               "expect": {"json": {"typee": "str"}}}])
        with pytest.raises(ValueError, match="json"):
            load_cases(p)

    def test_unknown_json_type(self, tmp_path):
        p = _write(tmp_path, [{"id": "a", "desc": "d", "messages": [{"role": "user", "content": "hi"}],
                               "expect": {"json": {"type": "float"}}}])
        with pytest.raises(ValueError, match="未知类型"):
            load_cases(p)

    def test_unknown_var_builder(self, tmp_path):
        p = _write(tmp_path, [{"id": "a", "desc": "d",
                               "messages": [{"role": "user", "content": "hi"}], "expect": {"nonempty": True},
                               "vars": {"X": {"random": {}}}}])
        with pytest.raises(ValueError, match="生成器"):
            load_cases(p)

    def test_error_message_carries_line_number(self, tmp_path):
        p = _write(tmp_path, [
            {"id": "a", "desc": "d", "messages": [{"role": "user", "content": "hi"}], "expect": {"nonempty": True}},
            {"id": "", "desc": "d", "messages": [{"role": "user", "content": "hi"}], "expect": {"nonempty": True}},
        ])
        with pytest.raises(ValueError, match="cases.jsonl:2"):
            load_cases(p)


class TestHaystack:
    def test_deterministic_and_sized(self):
        a = build_haystack(4000, 0.5, "ITE-7Q4X")
        b = build_haystack(4000, 0.5, "ITE-7Q4X")
        assert a == b                      # 无随机，可复现
        assert 3900 < len(a) < 4200
        assert a.count("ITE-7Q4X") == 1

    def test_key_position(self):
        assert build_haystack(4000, 0.0, "K").index("The access code is K") < 100
        assert len(build_haystack(4000, 0.9, "K")) - \
            build_haystack(4000, 0.9, "K").index("The access code is K") < 600


class TestRenderedMessages:
    def test_placeholder_replaced_original_untouched(self):
        case = _case({"contains": "K"},
                     vars={"DOC": {"haystack": {"chars": 500, "key_at": 0.5, "key": "K"}}},
                     messages=[{"role": "user", "content": "Doc:\n{DOC}\nfind key"}])
        rendered = case.rendered_messages()
        assert "{DOC}" not in rendered[0]["content"]
        assert "The access code is K" in rendered[0]["content"]
        assert case.messages[0]["content"] == "Doc:\n{DOC}\nfind key"   # 原始不改动


class TestCheck:
    def test_nonempty(self):
        _case({"nonempty": True}).check(_response(content="ok"))
        with pytest.raises(AssertionError, match="非空"):
            _case({"nonempty": True}).check(_response(content="  "))

    def test_contains_and_ignore_case(self):
        _case({"contains": "Banana", "ignore_case": True}).check(_response(content="BANANA"))
        with pytest.raises(AssertionError, match="包含"):
            _case({"contains": "banana"}).check(_response(content="apple"))

    def test_json_unparseable(self):
        with pytest.raises(AssertionError, match="合法 JSON"):
            _case({"json": {"type": "dict"}}).check(_response(content="not json"))

    def test_json_fences_stripped(self):
        _case({"json": {"type": "dict"}}).check(
            _response(content="```json\n{\"a\": 1}\n```"))

    def test_json_field_types(self):
        spec = {"type": "dict", "fields": {
            "name": {"type": "str", "nonempty": True}, "age": {"type": "int"}}}
        _case({"json": spec}).check(_response(content='{"name": "A", "age": 30}'))
        with pytest.raises(AssertionError, match="age"):
            _case({"json": spec}).check(_response(content='{"name": "A", "age": "30"}'))
        with pytest.raises(AssertionError, match="bool|类型"):
            _case({"json": spec}).check(_response(content='{"name": "A", "age": true}'))

    def test_json_array(self):
        spec = {"type": "list", "len": 3, "item_type": "str", "item_nonempty": True}
        _case({"json": spec}).check(_response(content='["red", "green", "blue"]'))
        with pytest.raises(AssertionError, match="长度"):
            _case({"json": spec}).check(_response(content='["red", "green"]'))
        with pytest.raises(AssertionError, match=r"json\[2\]"):
            _case({"json": spec}).check(_response(content='["red", "green", ""]'))

    def test_json_nested_path_eq_ignore_case(self):
        spec = {"path": "user.address.city", "eq": "shanghai", "nonempty": True}
        _case({"json": spec, "ignore_case": True}).check(
            _response(content='{"user": {"address": {"city": "Shanghai"}}}'))
        with pytest.raises(AssertionError, match="路径"):
            _case({"json": {"path": "user.zip"}}).check(
                _response(content='{"user": {}}'))

    def test_json_enum_in(self):
        _case({"json": {"path": "sentiment", "in": ["positive", "negative"]}}).check(
            _response(content='{"sentiment": "positive"}'))
        with pytest.raises(AssertionError, match="∈"):
            _case({"json": {"path": "sentiment", "in": ["positive"]}}).check(
                _response(content='{"sentiment": "neutral"}'))

    def test_tool_selection(self):
        _case({"tool": "get_weather"}).check(_response(tool_calls=_tool_call()))
        with pytest.raises(AssertionError, match="期望选择工具"):
            _case({"tool": "calculator"}).check(_response(tool_calls=_tool_call()))
        with pytest.raises(AssertionError, match="没有"):
            _case({"tool": "get_weather"}).check(_response(content="no tools"))

    def test_tool_args_contains(self):
        _case({"tool_args_contains": "tokyo"}).check(_response(tool_calls=_tool_call()))
        with pytest.raises(AssertionError, match="arguments"):
            _case({"tool_args_contains": "tokyo"}).check(
                _response(tool_calls=_tool_call(arguments="{bad json")))

    def test_missing_choices(self):
        with pytest.raises(AssertionError, match="没有"):
            _case({"tool": "x"}).check({})
