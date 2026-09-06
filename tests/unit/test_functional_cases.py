"""cases.jsonl 数据驱动核心单测（加载校验 / expect DSL / vars 渲染，全离线）。

API 契约断言部分含 fake SSE/错误 server 的 _post 端到端（真实 httpx 链路）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aite.functional.harness import (
    Case,
    _post,
    build_haystack,
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
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
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
