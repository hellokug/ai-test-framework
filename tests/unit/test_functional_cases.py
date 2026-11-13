"""cases.jsonl 数据驱动核心单测（加载校验 / expect DSL / vars 渲染，全离线）。

API 契约断言部分含 fake SSE/错误 server 的 _post 端到端（真实 httpx 链路）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aite.functional.harness import (
    Case,
    _merge_headers,
    _post,
    _redact_params,
    build_garbled,
    build_haystack,
    build_random_choice,
    build_random_number,
    build_random_string,
    expand_repeats,
    failure_detail,
    load_cases,
)


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


class TestRepeat:
    """repeat 字段：单条 jsonl 行按指定次数展开为多轮独立执行实例。"""

    def _case(self, **kw):
        raw = {"id": "t", "desc": "测试", "messages": [{"role": "user", "content": "hi"}],
               "expect": {"nonempty": True}}
        raw.update(kw)
        return Case(raw, "cases.jsonl:1")

    def test_default_and_null_mean_single_run(self):
        assert self._case().repeat == 1
        assert self._case(repeat=None).repeat == 1

    def test_bad_values_rejected(self):
        for bad in (0, -1, 1.5, True, "3", 101):
            with pytest.raises(ValueError, match="repeat"):
                self._case(repeat=bad)

    def test_expand_ids_and_indexes(self):
        runs = expand_repeats([self._case(repeat=3), self._case(id="u")])
        assert [r.id for r in runs] == ["t", "t[r2]", "t[r3]", "u"]
        assert [r.repeat_index for r in runs[:3]] == [1, 2, 3]
        assert runs[2].expect == runs[0].expect         # 请求/断言定义共享
        assert runs[2] is not runs[0]

    def test_expand_collision_detected(self):
        # 原 id 形如 x[r2] 会与 x 的展开产物相撞，加载期显式报错
        with pytest.raises(ValueError, match="冲突"):
            expand_repeats([self._case(id="x", repeat=3), self._case(id="x[r2]")])

    def test_load_cases_expands_repeat_lines(self, tmp_path):
        p = _write(tmp_path, [
            {"id": "a", "desc": "d", "repeat": 2,
             "messages": [{"role": "user", "content": "hi"}], "expect": {"nonempty": True}},
            {"id": "b", "desc": "d",
             "messages": [{"role": "user", "content": "hi"}], "expect": {"nonempty": True}},
        ])
        assert [c.id for c in load_cases(p)] == ["a", "a[r2]", "b"]

    def test_each_run_renders_vars_independently(self):
        # seed 缺省：每轮新随机值（先渲染原实例也不串轮——副本缓存已重置）
        def _var_case(spec):
            return self._case(repeat=2, vars={"N": spec},
                              messages=[{"role": "user", "content": "code {N}"}])

        fresh = _var_case({"random_string": {"length": 32}})
        assert fresh.rendered_messages()[0]["content"].startswith("code ")
        r1, r2 = expand_repeats([fresh])
        assert r1.rendered_messages()[0]["content"] != r2.rendered_messages()[0]["content"]
        seeded = _var_case({"random_string": {"length": 32, "seed": 7}})
        s1, s2 = expand_repeats([seeded])
        assert s1.rendered_messages() == s2.rendered_messages()   # 固定 seed 每轮一致


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


class TestVarBuilders:
    """vars 生成器族：确定性 / 参数校验 / 占位符渲染一致性（2026-10-16 低代码生成值）。"""

    def test_random_string_length_charset(self):
        s = build_random_string(length=32, seed=42)
        assert len(s) == 32 and s.isalnum()
        assert s == build_random_string(length=32, seed=42)          # seed 可复现
        assert build_random_string(length=8, seed=1) != build_random_string(length=8, seed=2)
        assert build_random_string(length=8, charset="digits", seed=1).isdigit()
        assert build_random_string(length=8, charset="hex", seed=1) ==             build_random_string(length=8, charset="hex", seed=1)
        with pytest.raises(ValueError, match="未知字符集"):
            build_random_string(length=8, charset="nope")
        with pytest.raises(ValueError, match="正整数"):
            build_random_string(length=0)

    def test_random_number_bounds(self):
        assert build_random_number(min=5, max=5, seed=1) == "5"
        for _ in range(20):
            v = int(build_random_number(min=3, max=9))
            assert 3 <= v <= 9
        with pytest.raises(ValueError, match="不得大于"):
            build_random_number(min=9, max=3)
        assert build_random_number(seed=7) == build_random_number(seed=7)

    def test_garbled_json_safe_and_seeded(self):
        g = build_garbled(length=200, seed=9)
        assert len(g) == 200
        json.dumps(g, ensure_ascii=False)         # 无代理区/控制字符，可安全落库
        assert g == build_garbled(length=200, seed=9)
        others = {build_garbled(length=64) for _ in range(8)}   # 无 seed → 彼此不同
        assert len(others) > 1

    def test_random_choice_and_errors(self):
        assert build_random_choice(options=["a"], seed=1) == "a"
        assert build_random_choice(options=[1, "x"], seed=3) in ("1", "x")
        with pytest.raises(ValueError, match="非空数组"):
            build_random_choice(options=[])

    def test_unknown_builder_and_missing_param(self):
        raw = {"id": "t", "desc": "d", "messages": [], "expect": {"nonempty": True},
               "vars": {"X": {"no_such_gen": {}}}}
        with pytest.raises(ValueError, match="未知 vars 生成器"):
            Case(raw, "cases.jsonl:1")
        raw["vars"] = {"X": {"random_string": {}}}
        with pytest.raises(ValueError, match="缺少参数"):
            Case(raw, "cases.jsonl:1")

    def test_rendered_memoized_within_execution(self):
        """同一次执行的发送与记录两处调用返回同一份（随机值一致）；原始 messages 不动。"""
        case = _case({"nonempty": True},
                     vars={"S": {"random_string": {"length": 16}}, "N": {"random_number": {"min": 0, "max": 9}}},
                     messages=[{"role": "user", "content": "id={S} n={N}"}])
        a = case.rendered_messages()[0]["content"]
        b = case.rendered_messages()[0]["content"]
        assert a == b
        assert "{S}" not in a and "{N}" not in a
        assert case.messages[0]["content"] == "id={S} n={N}"
        # 两个 Case 各自独立渲染（不同实例不同值，除非撞车——16 位字母数字撞车概率可忽略）
        other = _case({"nonempty": True},
                      vars={"S": {"random_string": {"length": 16}}},
                      messages=[{"role": "user", "content": "{S}"}])
        assert other.rendered_messages()[0]["content"] != case.rendered_messages()[0]["content"]


class TestJsonRelations:
    """json 断言关系：neq（不等于）/ contains（like 子串包含）。"""

    def _resp(self, content):
        return _response(content=content)

    def test_neq(self):
        spec = {"json": {"path": "usage.total_tokens", "neq": 0}}
        case = _case(spec)
        case.check(_response(content='{"usage": {"total_tokens": 5}}'))
        with pytest.raises(AssertionError, match="期望 !="):
            case.check(_response(content='{"usage": {"total_tokens": 0}}'))

    def test_contains_like(self):
        case = _case({"json": {"path": "id", "contains": "req-"}})
        case.check(_response(content='{"id": "req-abc-123"}'))
        with pytest.raises(AssertionError, match="期望包含"):
            case.check(_response(content='{"id": "abc-123"}'))
        # 非字符串目标转 JSON 串后包含；ignore_case 生效
        _case({"json": {"contains": "ABC"}, "ignore_case": True}).check(self._resp('"xyzabc"'))
        _case({"json": {"contains": 7}}).check(self._resp('{"n": 7}'))

    def test_new_keys_pass_validation(self):
        _case({"json": {"path": "a", "neq": "x", "contains": "y"}})


class TestHttpStatusRelations:
    """http_status 关系形式：{neq/gte/lte/in: 值}（int 兼容 == 不变）。"""

    def test_relations(self):
        _case({"http_status": {"neq": 200}}).check({"_status": 404, "_body": "nf"})
        with pytest.raises(AssertionError, match="期望 HTTP !="):
            _case({"http_status": {"neq": 200}}).check({"_status": 200, "_body": "ok"})
        _case({"http_status": {"gte": 500}}).check({"_status": 503, "_body": ""})
        with pytest.raises(AssertionError, match="期望 HTTP >="):
            _case({"http_status": {"gte": 500}}).check({"_status": 404, "_body": ""})
        _case({"http_status": {"lte": 299}}).check({"_status": 204, "_body": ""})
        _case({"http_status": {"in": [400, 422]}}).check({"_status": 422, "_body": ""})
        with pytest.raises(AssertionError, match="期望 HTTP ∈"):
            _case({"http_status": {"in": [400, 422]}}).check({"_status": 500, "_body": ""})

    def test_relation_validation(self):
        for bad in ({"nope": 200}, {"eq": 99}, {"eq": 200, "neq": 300},
                    {"in": []}, {"in": [200.5]}, {"gte": "5xx"}):
            with pytest.raises(ValueError, match="http_status"):
                _case({"http_status": bad})
        _case({"http_status": {"in": [400, 422]}})   # 合法不抛
        _case({"http_status": 413})                  # int 兼容不变


class TestParamsRendering:
    """vars 占位符扩展到 params（input 等自定义顶层字段的低代码生成值）。"""

    def test_params_placeholder_replaced(self):
        case = _case({"nonempty": True},
                     vars={"S": {"random_string": {"length": 8, "seed": 1}}},
                     messages=[{"role": "user", "content": "{S}"}],
                     params={"input": "txt-{S}", "temperature": 0.5})
        rp = case.rendered_params()
        assert rp["input"] != "txt-{S}" and "txt-" in rp["input"]
        assert len(rp["input"].split("-", 1)[1]) == 8
        assert rp["temperature"] == 0.5               # 非字符串值原样保留
        assert case.params["input"] == "txt-{S}"      # 原始 params 不改动

    def test_messages_and_params_share_values(self):
        """同一次执行内同占位符在 messages 与 params 中同值（同源生成值）。"""
        case = _case({"nonempty": True},
                     vars={"S": {"random_string": {"length": 16}}},
                     messages=[{"role": "user", "content": "{S}"}],
                     params={"input": "{S}"})
        m = case.rendered_messages()[0]["content"]
        p = case.rendered_params()["input"]
        assert m == p and len(m) == 16
        # 缓存一致：再次调用同值（随机生成器同次执行不漂移）
        assert case.rendered_params()["input"] == p

    def test_no_vars_params_untouched(self):
        case = _case({"nonempty": True}, params={"input": "{S}"})
        assert case.rendered_params()["input"] == "{S}"   # 无 vars → 原样透传


class TestRecordResultSnapshot:
    """record_result 快照：params/tools 随用例记录（复现 curl 数据源）。"""

    def test_record_includes_params_and_tools(self, tmp_path, monkeypatch):
        results_file = tmp_path / "results.jsonl"
        monkeypatch.setenv("AITE_FT_RESULTS", str(results_file))
        from aite.functional.harness import CaseItem
        case = _case({"nonempty": True},
                     tools=[{"type": "function", "function": {"name": "get_weather"}}],
                     params={"tool_choice": "auto"})
        item = CaseItem.__new__(CaseItem)          # 只借用 record_result（case/_last_response 两个字段）
        item.case = case
        item._last_response = {"choices": [{"message": {"role": "assistant", "content": "ok"},
                                            "finish_reason": "stop"}]}
        CaseItem.record_result(item, "passed", None)
        entry = json.loads(results_file.read_text(encoding="utf-8").strip())
        assert entry["params"] == {"tool_choice": "auto"}
        assert entry["tools"][0]["function"]["name"] == "get_weather"

    def test_record_redacts_sensitive_headers(self, tmp_path, monkeypatch):
        """落库快照里 _headers 敏感头值打码（复现凭证走环境注入）。"""
        results_file = tmp_path / "results.jsonl"
        monkeypatch.setenv("AITE_FT_RESULTS", str(results_file))
        from aite.functional.harness import CaseItem
        case = _case({"http_status": 401},
                     params={"_headers": {"Authorization": "Bearer bad", "X-Tag": "t"}})
        item = CaseItem.__new__(CaseItem)
        item.case = case
        item._last_response = {"_status": 401, "_body": {"error": {"message": "auth"}}}
        CaseItem.record_result(item, "passed", None)
        entry = json.loads(results_file.read_text(encoding="utf-8").strip())
        assert entry["params"]["_headers"] == {"Authorization": "******", "X-Tag": "t"}

    def test_error_compact_keeps_exception_lines(self, tmp_path, monkeypatch):
        """超长 traceback 落库：E 行置顶 + 保尾，真实失败原因不被框架代码淹没。"""
        from aite.functional.harness import CaseItem, _compact_longrepr
        results_file = tmp_path / "results.jsonl"
        monkeypatch.setenv("AITE_FT_RESULTS", str(results_file))
        case = _case({"http_status": 401})
        item = CaseItem.__new__(CaseItem)
        item.case = case
        item._last_response = None
        frame = "".join(f"    framework filler line {i}\n" for i in range(300))
        longrepr = (f"{frame}>   result = func()\n"
                    "E   AssertionError: 预期收到 HTTP 错误码，实际网络失败："
                    "transport error: Illegal header value b' Bearer x '")
        CaseItem.record_result(item, "failed", longrepr)
        entry = json.loads(results_file.read_text(encoding="utf-8").strip())
        err = entry["error"]
        assert "关键信息" in err and "Illegal header value" in err
        assert len(err) <= 2000 + 100                  # 总长受控
        assert err.rstrip().endswith("Bearer x '")     # 尾部完整保留到最终异常行
        # 纯函数边界：短文本原样、None、无 E 行保尾
        assert _compact_longrepr("短错误") == "短错误"
        assert _compact_longrepr(None) is None
        blob = "x" * 3000
        assert _compact_longrepr(blob) == blob[-2000:]


class TestCheck:
    def test_max_len(self):
        _case({"max_len": 5}).check(_response("hello"))
        with pytest.raises(AssertionError, match="期望回复"):
            _case({"max_len": 5}).check(_response("hello world"))
        with pytest.raises(ValueError, match="max_len"):
            _case({"max_len": 0})

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

    def test_json_empty_spec_means_parse_only(self):
        """json: {} = 仅校验剥围栏后可解析（JSON 输出格式正确性）。"""
        _case({"json": {}}).check(_response(content='{"any": [1, 2, 3]}'))
        _case({"json": {}}).check(_response(content='```json\n[1, 2]\n```'))
        with pytest.raises(AssertionError, match="合法 JSON"):
            _case({"json": {}}).check(_response(content="not json at all"))


class TestQualityChecks:
    """质量类断言：no_garbled / no_leak / no_repeat（保守口径，正常文本不误报）。"""

    # ---- 乱码 ----

    def test_garbled_clean_text_passes(self):
        _case({"no_garbled": True}).check(_response(content="你好，世界！Hello world."))
        _case({"no_garbled": True}).check(_response(content="château naïve café"))  # 正常重音

    def test_garbled_detects_each_kind(self):
        bad_texts = [
            "含\ufffd乱码",                 # U+FFFD 替换符
            "出现锟斤拷了",                  # GBK 乱码
            "坏\x00字符",                   # C0 控制符
            "坏\x1f字符",
            "DEL\x7f",
            "C1\x85控制",                   # C1 控制符
            "æµ‹è¯•ä¸­æ–‡",                # UTF-8 按 Latin-1 误解码
        ]
        for bad in bad_texts:
            with pytest.raises(AssertionError, match="乱码"):
                _case({"no_garbled": True}).check(_response(content=bad))

    # ---- 内部标签泄露 ----

    def test_leak_default_patterns(self):
        bad_texts = [
            "<|im_start|>assistant",
            "回答完毕<|endoftext|>",
            "<｜begin▁of▁sentence｜>你好",   # DeepSeek 全角变体
            "[INST] what is 2+2 [/INST]",
            "<<SYS>>you are helpful<</SYS>>",
            "让我想想<think>3+2=5</think>答案是 5",
            "<tool_call>{\"name\": \"f\"}</tool_call>",
        ]
        for bad in bad_texts:
            with pytest.raises(AssertionError, match="泄露"):
                _case({"no_leak": True}).check(_response(content=bad))

    def test_leak_normal_html_not_flagged(self):
        _case({"no_leak": True}).check(
            _response(content="可以用 <div> 或 <b> 标签写 HTML，如 <br/> 换行。"))

    def test_leak_extra_patterns(self):
        case = _case({"no_leak": {"extra_patterns": [r"<MY-TAG-\d+>"]}})
        with pytest.raises(AssertionError, match="泄露"):
            case.check(_response(content="x<MY-TAG-7>y"))
        _case({"no_leak": {"extra_patterns": [r"<MY-TAG-\d+>"]}}).check(
            _response(content="clean text"))

    def test_leak_bad_shape_rejected_at_load(self, tmp_path):
        for bad_value in ("yes", 1, {"patterns": ["x"]}, {"extra_patterns": ["("]}):
            p = _write(tmp_path, [
                {"id": "a", "desc": "d",
                 "messages": [{"role": "user", "content": "hi"}],
                 "expect": {"no_leak": bad_value}}])
            with pytest.raises(ValueError, match="no_leak"):
                load_cases(p)

    # ---- 复读 ----

    def test_repeat_long_block_loop_detected(self):
        with pytest.raises(AssertionError, match="复读"):
            _case({"no_repeat": True}).check(_response(content="我喜欢吃苹果和香蕉" * 4))

    def test_repeat_short_block_loop_detected(self):
        with pytest.raises(AssertionError, match="复读"):
            _case({"no_repeat": True}).check(_response(content="哈哈哈" * 12))

    def test_repeat_repeated_lines_detected(self):
        line = "这是一行足够长的重复内容，用来触发整行复读检测。"
        with pytest.raises(AssertionError, match="复读"):
            _case({"no_repeat": True}).check(_response(content="\n".join([line] * 5)))

    def test_repeat_normal_text_passes(self):
        _case({"no_repeat": True}).check(_response(
            content="哈哈，这个笑话真好笑，哈哈哈哈。总之今天天气不错，我们继续下个话题吧。"))
        _case({"no_repeat": True}).check(_response(
            content="第一段介绍基本能力。第二段说明测试方法。第三段给出结论与建议。总结：一切正常。"))
        _case({"no_repeat": True}).check(_response(
            content="repeat repeat repeat"))   # 3 次短块不触发（阈值 ≥4）

    # ---- 组合 ----

    def test_combined_quality_expectations(self):
        expect = {"nonempty": True, "no_garbled": True, "no_leak": True, "no_repeat": True}
        _case(expect).check(_response(content="正常回复：一切正常。"))
        with pytest.raises(AssertionError, match="泄露"):
            _case(expect).check(_response(content="<|im_end|>正常回复"))


class TestFailureDetail:
    """失败详情组装：断言→输入→输出，体积有预算。"""

    def _detail(self, case, response) -> str:
        try:
            case.check(response)
            raise AssertionError("应该失败")
        except AssertionError as exc:
            if str(exc) == "应该失败":
                raise
            return failure_detail(case, response, exc)

    def test_contains_assertion_input_output(self):
        case = _case({"contains": "yes"})
        detail = self._detail(case, _response(content="No"))
        assert "[t] 测试" in detail
        assert "断言失败:" in detail and "'yes'" in detail
        assert "输入 | user: hi" in detail          # 请求体可见
        assert "输出 | No" in detail                 # 模型回复可见

    def test_long_input_truncated_with_total(self):
        case = _case({"contains": "KEY"},
                     vars={"DOC": {"haystack": {"chars": 16000, "key_at": 0.5, "key": "K"}}},
                     messages=[{"role": "user", "content": "{DOC}"}])
        detail = self._detail(case, _response(content="wrong"))
        assert "共" in detail and "中段省略" in detail   # 标注总长
        assert len(detail) < 1200                     # 体积预算

    def test_tool_call_output_when_content_empty(self):
        case = _case({"tool": "calculator"})
        detail = self._detail(case, _response(tool_calls=_tool_call(
            name="get_weather", arguments='{"city": "Tokyo"}')))
        assert "tool_call: get_weather" in detail
        assert "city" in detail

    def test_empty_output_marked(self):
        case = _case({"nonempty": True})
        detail = self._detail(case, _response(content=""))
        assert "输出 | <空>" in detail

    def test_no_response_marks_nothing(self):
        case = _case({"contains": "x"})
        try:
            case.check(_response(content="y"))
        except AssertionError as exc:
            detail = failure_detail(case, None, exc)
        assert "输入 |" in detail and "输出" not in detail


class TestApiContractChecks:
    """API 契约类断言：not_contains / no_tool / finish_reason / resp /
    http_status（预期错误模式）/ chunks_min / sse_done。"""

    # ---- not_contains ----

    def test_not_contains_pass_and_fail(self):
        _case({"not_contains": "塔"}).check(_response(content="巴黎是法国首都"))
        with pytest.raises(AssertionError, match="不包含"):
            _case({"not_contains": "塔"}).check(_response(content="埃菲尔铁塔很高"))

    # ---- no_tool / finish_reason ----

    def test_no_tool(self):
        ok = {"choices": [{"message": {"content": "plain", "tool_calls": []}}]}
        _case({"no_tool": True}).check(ok)
        with pytest.raises(AssertionError, match="不发起 tool call"):
            _case({"no_tool": True}).check(_response(tool_calls=_tool_call()))

    def test_finish_reason(self):
        ok = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
        _case({"finish_reason": "stop"}).check(ok)
        length = {"choices": [{"message": {"content": "hi"}, "finish_reason": "length"}]}
        with pytest.raises(AssertionError, match="finish_reason"):
            _case({"finish_reason": "stop"}).check(length)
        with pytest.raises(AssertionError, match="finish_reason"):
            _case({"finish_reason": "stop"}).check(_response(content="hi"))  # 无字段

    # ---- resp 响应体断言 ----

    def test_resp_path_type_eq(self):
        resp = {"id": "chatcmpl-1", "usage": {"total_tokens": 42,
                                              "prompt_tokens": 10}}
        _case({"resp": {"path": "id", "type": "str", "nonempty": True}}).check(resp)
        _case({"resp": {"path": "usage.total_tokens", "eq": 42}}).check(resp)
        with pytest.raises(AssertionError, match="路径"):
            _case({"resp": {"path": "usage.model"}}).check(resp)
        with pytest.raises(AssertionError, match="期望 =="):
            _case({"resp": {"path": "usage.total_tokens", "eq": 43}}).check(resp)
        with pytest.raises(AssertionError, match="类型"):
            _case({"resp": {"path": "id", "type": "int"}}).check(resp)

    def test_resp_numeric_index_and_nonempty_list(self):
        resp = {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 42}}
        _case({"resp": {"path": "choices.0.message.role", "eq": "assistant"}}).check(resp)
        _case({"resp": {"path": "choices", "nonempty": True}}).check(resp)   # 非空 list
        with pytest.raises(AssertionError, match="越界"):
            _case({"resp": {"path": "choices.3"}}).check(resp)
        with pytest.raises(AssertionError, match="缺失"):
            _case({"resp": {"path": "choices.0.foo"}}).check(resp)
        empty = {"choices": []}
        with pytest.raises(AssertionError, match="非空"):
            _case({"resp": {"path": "choices", "nonempty": True}}).check(empty)

    def test_resp_array_spec_multiple_fields(self):
        """resp 数组形态：一条用例断言响应体多个字段。"""
        resp = {"id": "x", "object": "chat.completion", "created": 123}
        _case({"resp": [{"path": "id", "type": "str"},
                        {"path": "created", "type": "int"}]}).check(resp)
        with pytest.raises(AssertionError, match="created"):
            _case({"resp": [{"path": "id", "type": "str"},
                             {"path": "created", "type": "str"}]}).check(resp)

    # ---- 预期错误模式（http_status）----

    def test_http_status_expected_error(self):
        case = _case({"http_status": 400})
        case.check({"_status": 400, "_body": {"error": {"message": "bad"}}})
        with pytest.raises(AssertionError, match="期望 HTTP 400，实际 500"):
            case.check({"_status": 500, "_body": {"error": "oops"}})

    def test_error_response_without_http_status_expect(self):
        """错误响应但用例没配 http_status → 明确报错（配置缺口）。"""
        with pytest.raises(AssertionError, match="未配置 http_status"):
            _case({"nonempty": True}).check({"_status": 400, "_body": {}})

    def test_http_status_with_resp_body_assertion(self):
        case = _case({"http_status": 404,
                      "resp": {"path": "error.type", "eq": "model_not_found"}})
        case.check({"_status": 404, "_body": {"error": {"type": "model_not_found"}}})
        with pytest.raises(AssertionError, match="eq"):
            case.check({"_status": 404, "_body": {"error": {"type": "invalid_request"}}})

    # ---- 流式（伪响应结构）----

    def _stream_resp(self, n_chunks=3, done=True, content="hello", finish="stop"):
        return {"choices": [{"message": {"role": "assistant", "content": content},
                             "finish_reason": finish}],
                "_chunks": n_chunks, "_sse_ok": done}

    def test_chunks_min_and_sse_done(self):
        _case({"chunks_min": 3, "sse_done": True, "nonempty": True,
               "finish_reason": "stop"}).check(self._stream_resp())
        with pytest.raises(AssertionError, match="chunk 数"):
            _case({"chunks_min": 5}).check(self._stream_resp(n_chunks=3))
        with pytest.raises(AssertionError, match=r"\[DONE\]"):
            _case({"sse_done": True}).check(self._stream_resp(done=False))

    # ---- 加载期形状校验 ----

    def test_bad_api_contract_shapes_rejected(self, tmp_path):
        bad_expects = [
            {"http_status": "400"}, {"http_status": 999},
            {"finish_reason": "paused"},
            {"resp": "id"}, {"resp": {"type": "float"}},
            {"chunks_min": 0}, {"chunks_min": "2"}, {"sse_done": "yes"},
        ]
        for bad in bad_expects:
            p = _write(tmp_path, [{"id": "a", "desc": "d",
                                   "messages": [{"role": "user", "content": "hi"}],
                                   "expect": bad}])
            with pytest.raises(ValueError):
                load_cases(p)

    def test_error_mode_detail_output(self):
        """失败详情：预期错误模式显示 HTTP 码与错误体。"""
        case = _case({"http_status": 400})
        try:
            case.check({"_status": 500, "_body": {"error": "boom"}})
            raise AssertionError("unreachable")
        except AssertionError as exc:
            detail = failure_detail(case, {"_status": 500, "_body": {"error": "boom"}}, exc)
        assert "HTTP 500" in detail

    def test_stream_detail_output(self):
        """失败详情：流式响应显示 chunk 数与收尾状态。"""
        case = _case({"contains": "world"})
        try:
            case.check(self._stream_resp(content="hello"))
            raise AssertionError("unreachable")
        except AssertionError as exc:
            detail = failure_detail(case, self._stream_resp(content="hello"), exc)
        assert "stream: 3 chunks" in detail and "sse_done=True" in detail


class _FakeApiHandler(BaseHTTPRequestHandler):
    """按 stream/错误码分流的最小 chat/completions 端点。"""

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        self.server.last_headers = {k.lower(): v for k, v in self.headers.items()}
        if self.headers.get("Authorization") != "Bearer EMPTY":
            self._send(401, {"error": {"message": "Authentication Fails"}})
            return
        if body.get("model") == "no-such-model":
            self._send(404, {"error": {"type": "model_not_found"}})
            return
        if body.get("stream"):
            chunks = [
                {"choices": [{"delta": {"role": "assistant"}}]},
                {"choices": [{"delta": {"content": "Hello"}}]},
                {"choices": [{"delta": {"content": " world"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
            payload = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload.encode())))
            self.end_headers()
            self.wfile.write(payload.encode())
        else:
            self._send(200, {"choices": [{"message": {"content": "hi"},
                                          "finish_reason": "stop"}]})

    def _send(self, status, obj):
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class TestPostAgainstFakeServer:
    """_post 请求层端到端（fake server）：流式 SSE 解析 / 预期错误模式。"""

    @pytest.fixture()
    def server(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeApiHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield srv
        srv.shutdown()

    @pytest.fixture()
    def env(self, server, monkeypatch):
        import httpx

        monkeypatch.setenv("AITE_FT_BASE_URL",
                           f"http://127.0.0.1:{server.server_address[1]}/v1")
        monkeypatch.setenv("AITE_FT_MODEL", "fake-model")
        monkeypatch.setenv("AITE_FT_API_KEY", "EMPTY")
        client = httpx.Client(timeout=10)
        yield client
        client.close()

    def test_stream_parsing(self, env):
        resp = _post(env, [{"role": "user", "content": "hi"}], tools=None,
                     params={"stream": True})
        assert resp["choices"][0]["message"]["content"] == "Hello world"
        assert resp["choices"][0]["finish_reason"] == "stop"
        assert resp["_chunks"] == 4
        assert resp["_sse_ok"] is True
        _case({"nonempty": True, "finish_reason": "stop", "chunks_min": 2,
               "sse_done": True, "contains": "world"}).check(resp)

    def test_expected_error_mode(self, env):
        resp = _post(env, [{"role": "user", "content": "hi"}], tools=None,
                     params={"model": "no-such-model"}, expect_error=True)
        assert resp["_status"] == 404
        _case({"http_status": 404,
               "resp": {"path": "error.type", "eq": "model_not_found"}}).check(resp)

    def test_unexpected_error_raises(self, env):
        """没配 http_status 的用例收到错误响应 → httpx raise_for_status 抛出。"""
        import httpx

        with pytest.raises(httpx.HTTPStatusError):
            _post(env, [{"role": "user", "content": "hi"}], tools=None,
                  params={"model": "no-such-model"})

    def test_request_trace_is_actual_body(self, env):
        """_request 留痕 = 实际发出的请求体（详情页原样展示的数据源）；控制键不进 body。"""
        msgs = [{"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
        resp = _post(env, msgs, tools=tools,
                     params={"temperature": 0, "_headers": {"X-Trace": "1"}})
        req = resp["_request"]
        assert req["method"] == "POST" and req["url"].endswith("/v1/chat/completions")
        assert req["body"] == {"model": "fake-model", "messages": msgs,
                               "temperature": 0, "tools": tools}
        assert resp["choices"][0]["message"]["content"] == "hi"

    def test_empty_tools_sent(self, env):
        """tools=[] 是显式配置：必须原样发出（空数组 → 400 类参数校验用例依赖）。"""
        resp = _post(env, [{"role": "user", "content": "hi"}], tools=[], params={})
        assert resp["_request"]["body"]["tools"] == []

    def test_request_trace_headers_masked(self, env, server):
        """_request.headers = 实际发送的合并头（默认鉴权 ⊕ 自定义），敏感值留痕打码。"""
        resp = _post(env, [{"role": "user", "content": "hi"}], tools=None,
                     params={"_headers": {"X-Trace": "1", "X-Api-Key": "secret-value"}})
        hdr = resp["_request"]["headers"]
        assert hdr["Authorization"] == "******"   # 默认鉴权头是敏感头，留痕打码
        assert hdr["X-Api-Key"] == "******"       # 自定义敏感头同样打码
        assert hdr["X-Trace"] == "1"              # 非敏感自定义头保真
        assert server.last_headers["authorization"] == "Bearer EMPTY"    # 实际发送不脱敏
        assert server.last_headers["x-api-key"] == "secret-value"
        assert server.last_headers["x-trace"] == "1"

    def test_request_trace_stream_error_and_raw_body(self, env):
        msgs = [{"role": "user", "content": "hi"}]
        stream = _post(env, msgs, tools=None, params={"stream": True})
        assert stream["_request"]["body"]["stream"] is True
        _case({"sse_done": True}).check(stream)
        err = _post(env, msgs, tools=None, params={"model": "no-such-model"},
                    expect_error=True)
        assert err["_status"] == 404
        assert err["_request"]["body"]["model"] == "no-such-model"
        raw = _post(env, msgs, tools=None, params={"_raw_body": "not json{"})
        assert raw["_request"]["body"] == "not json{"   # 原始字符串，不是 payload


class TestHeadersControl:
    """_headers 请求构造控制：合并/覆盖/null 删除 + 加载校验 + 落库脱敏。"""

    @pytest.fixture()
    def env_key(self, monkeypatch):
        monkeypatch.setenv("AITE_FT_API_KEY", "test-key")

    def test_merge_default_only(self, env_key):
        assert _merge_headers(None) == {"Authorization": "Bearer test-key"}

    def test_merge_override_case_insensitive(self, env_key):
        """小写键覆盖默认 Authorization：不产生重复头。"""
        headers = _merge_headers({"authorization": "Bearer bad"})
        assert list(headers) == ["Authorization"]
        assert headers["Authorization"] == "Bearer bad"

    def test_merge_null_deletes(self, env_key):
        assert _merge_headers({"Authorization": None}) == {}

    def test_merge_extra_headers(self, env_key):
        headers = _merge_headers({"X-Trace": "t"})
        assert headers == {"Authorization": "Bearer test-key", "X-Trace": "t"}

    def test_invalid_headers_rejected_at_load(self):
        for bad in (["Authorization"], {"Authorization": 123}):
            with pytest.raises(ValueError, match="params._headers"):
                _case({"nonempty": True}, params={"_headers": bad})

    def test_params_must_be_object(self):
        with pytest.raises(ValueError, match="params 须为对象"):
            _case({"nonempty": True}, params="temperature=0")

    def test_redact_masks_sensitive_keeps_plain(self):
        params = {"_headers": {"Authorization": "Bearer real-key", "X-Api-Key": "k",
                               "x-api-key": "k2", "X-Tag": "t"}}
        masked = _redact_params(params)
        assert masked["_headers"] == {"Authorization": "******", "X-Api-Key": "******",
                                      "x-api-key": "******", "X-Tag": "t"}
        assert params["_headers"]["Authorization"] == "Bearer real-key"   # 原始不改动

    def test_redact_untouched_without_headers(self):
        params = {"temperature": 0.5}
        assert _redact_params(params) is params


class TestPostHeadersEndToEnd:
    """_headers 端到端（fake server 按鉴权分流 401/200，捕获实际收到头）。"""

    @pytest.fixture()
    def server(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeApiHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield srv
        srv.shutdown()

    @pytest.fixture()
    def env(self, server, monkeypatch):
        import httpx

        monkeypatch.setenv("AITE_FT_BASE_URL",
                           f"http://127.0.0.1:{server.server_address[1]}/v1")
        monkeypatch.setenv("AITE_FT_MODEL", "fake-model")
        monkeypatch.setenv("AITE_FT_API_KEY", "EMPTY")
        client = httpx.Client(timeout=10)
        yield client
        client.close()

    def test_override_auth_fails_401(self, env):
        """坏 key 覆盖 Authorization → 401（认证负向用例主路径）。"""
        resp = _post(env, [{"role": "user", "content": "hi"}], tools=None,
                     params={"_headers": {"Authorization": "Bearer bad-key"}},
                     expect_error=True)
        assert resp["_status"] == 401
        _case({"http_status": 401,
               "resp": {"path": "error.message", "eq": "Authentication Fails"}}).check(resp)

    def test_removed_auth_fails_401(self, env):
        """Authorization: null 删头 → 401（覆盖语义表达不了删头）。"""
        resp = _post(env, [{"role": "user", "content": "hi"}], tools=None,
                     params={"_headers": {"Authorization": None}},
                     expect_error=True)
        assert resp["_status"] == 401

    def test_stream_inherits_merged_headers(self, env):
        """流式路径同样吃合并后的头。"""
        resp = _post(env, [{"role": "user", "content": "hi"}], tools=None,
                     params={"stream": True, "_headers": {"Authorization": None}},
                     expect_error=True)
        assert resp["_status"] == 401

    def test_custom_header_sent_and_default_auth_kept(self, env, server):
        _post(env, [{"role": "user", "content": "hi"}], tools=None,
              params={"_headers": {"X-Trace-Id": "t-1"}})
        assert server.last_headers["authorization"] == "Bearer EMPTY"
        assert server.last_headers["x-trace-id"] == "t-1"

    def test_raw_body_content_type_overridable(self, env, server):
        """_raw_body 的 text/plain 让位于用户自带 Content-Type。"""
        _post(env, [{"role": "user", "content": "hi"}], tools=None,
              params={"_raw_body": "not json", "_headers": {"Content-Type": "application/xml"}})
        assert server.last_headers["content-type"] == "application/xml"

    def test_raw_body_default_content_type(self, env, server):
        _post(env, [{"role": "user", "content": "hi"}], tools=None,
              params={"_raw_body": "not json"})
        assert server.last_headers["content-type"] == "text/plain"


class TestRespContains:
    """resp 断言 contains 关系（错误体模糊匹配，低代码「错误体字段」芯片）。"""

    def test_resp_contains_on_error_response(self):
        case = _case({"http_status": 400,
                      "resp": {"path": "error.message", "contains": "Model"}})
        case.check({"_status": 400, "_body": {"error": {"message": "Model not exist"}}})
        with pytest.raises(AssertionError, match="期望包含"):
            case.check({"_status": 400, "_body": {"error": {"message": "bad request"}}})

    def test_resp_contains_non_string_target(self):
        case = _case({"http_status": 422, "resp": {"path": "error.param", "contains": "temperature"}})
        case.check({"_status": 422, "_body": {"error": {"param": ["temperature", "top_p"]}}})


def test_case_meta_passthrough_for_backfill():
    """用例级标签（用例 id/类型/等级）引擎不消费，随执行记录透传；空值不带。"""
    from aite.functional.harness import Case
    case = Case({"id": "r1", "desc": "d", "messages": [], "expect": {"nonempty": True},
                 "case_id": "TC-9", "type": "边界", "level": ""}, "t")
    assert case.meta == {"case_id": "TC-9", "type": "边界"}
