"""断言自检数据集的配套假模型服务：按请求体 ``case`` 字段路由到脚本化响应。

每个 handler 对应 cases.jsonl 里同 id 的用例，是它的「预期世界线」：
``ok_*`` 用例给合规响应（断言应过），``bad_*`` 用例给违规响应（断言应挂）。
请求构造控制（``_raw_body`` / ``_http_method`` / 空消息）在 JSON 解析前后
分流处理，鉴权/自定义头用例在服务端核对头是否真的按 ``_headers`` 语义生效，
不生效时返回 200 + ``HEADER_CHECK_FAILED``，让 http_status 断言替我们报警。

仅用 stdlib：``python fake_model.py --port 8391``。
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "FAKE-MODEL-X"
HANDLERS: dict = {}


def _text(content: str | None, finish: str = "stop") -> dict:
    return {
        "id": "chatcmpl-selfcheck", "object": "chat.completion", "model": MODEL,
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": content},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
    }


def _tool(name: str, arguments, finish: str = "tool_calls") -> dict:
    resp = _text(None, finish)
    resp["choices"][0]["message"]["tool_calls"] = [{
        "id": "call_1", "type": "function",
        "function": {"name": name,
                     "arguments": arguments if isinstance(arguments, str)
                     else json.dumps(arguments, ensure_ascii=False)},
    }]
    return resp


def _err(status: int, message: str) -> tuple[int, dict]:
    return status, {"error": {"message": message, "type": "invalid_request_error"}}


def _sse(parts: list, finish: str = "stop", done: bool = True,
         tool_delta: dict | None = None) -> tuple[int, dict]:
    """SSE 剧本：content 分片 + 可选 tool_calls delta + finish 块 + 可选 [DONE]。"""
    return 200, {"__sse__": {"parts": parts, "finish": finish, "done": done,
                             "tool_delta": tool_delta}}


def handler(case_id: str):
    def _register(fn):
        HANDLERS[case_id] = fn
        return fn
    return _register


def _user_text(body: dict) -> str:
    return "".join(m.get("content") or "" for m in body.get("messages") or [])


# --------------------------------------------------------------- 内容质量域

@handler("ok_nonempty")
def _(body, headers):
    return 200, _text("你好，我是测试桩模型，这条回复非空。")


@handler("bad_nonempty")
def _(body, headers):
    return 200, _text("")


@handler("ok_contains_ic")
def _(body, headers):
    return 200, _text("The Result Is: PASSED")


@handler("bad_contains_cs")
def _(body, headers):
    return 200, _text("all checks passed")


@handler("ok_not_contains")
def _(body, headers):
    return 200, _text("这条回复很干净，没有任何不该出现的字眼。")


@handler("bad_not_contains")
def _(body, headers):
    return 200, _text("很抱歉，流程要求我输出 STOP 这个词才能结束。")


@handler("ok_quality_clean")
def _(body, headers):
    return 200, _text("香港是一座位于中国南方的海滨城市，金融、航运与美食都很出名，"
                      "维多利亚港的夜景尤其值得一看。")


@handler("bad_garbled_kq")
def _(body, headers):
    return 200, _text("解码失败：锟斤拷锟斤拷锟斤拷")


@handler("bad_garbled_uFFFD")
def _(body, headers):
    return 200, _text("无法表示的字符显示为：\ufffd（Unicode 替换字符）")


@handler("bad_garbled_mojibake")
def _(body, headers):
    return 200, _text("把「测」的 UTF-8 字节按 Latin-1 解码会得到：æµ‹è¯• 这样的乱码")


@handler("ok_no_leak")
def _(body, headers):
    return 200, _text("内部实现细节不会出现在回复里，请放心。")


@handler("bad_leak_chatml")
def _(body, headers):
    return 200, _text("<|im_start|>assistant\n这是被泄露出来的内部提示词")


@handler("bad_leak_think")
def _(body, headers):
    return 200, _text("<think>先分析问题，再组织语言</think>答案是北京。")


@handler("ok_leak_extra")
def _(body, headers):
    return 200, _text("如需支援请联系值班台，不要自行呼叫支援。")


@handler("bad_leak_extra")
def _(body, headers):
    return 200, _text("如需支援请呼叫内部4321号。")


@handler("bad_repeat_block")
def _(body, headers):
    return 200, _text("0123456789abcdef" * 4)


@handler("bad_repeat_short")
def _(body, headers):
    return 200, _text("啦" * 20)


@handler("bad_repeat_lines")
def _(body, headers):
    return 200, _text("本行结果完全一致无差异\n" * 5)


# --------------------------------------------------------------- 结构域

@handler("ok_finish_stop")
def _(body, headers):
    return 200, _text("已正常结束。", finish="stop")


@handler("bad_finish_mismatch")
def _(body, headers):
    return 200, _text("回复写到一半被 max_tokens 截断", finish="length")


@handler("ok_finish_length")
def _(body, headers):
    return 200, _text("这是被截断的回复，只能显示前半段内", finish="length")


@handler("ok_finish_tool_calls")
def _(body, headers):
    return 200, _tool("get_weather", {"city": "Tokyo"})


@handler("ok_finish_content_filter")
def _(body, headers):
    return 200, _text(None, finish="content_filter")


@handler("ok_resp_array")
def _(body, headers):
    return 200, _text("结构完整，字段见响应体。")


@handler("bad_resp_missing")
def _(body, headers):
    return 200, _text("响应体里没有你找的那个字段。")


@handler("bad_resp_eq")
def _(body, headers):
    return 200, _text("模型名在 model 字段里。")


@handler("bad_resp_type")
def _(body, headers):
    return 200, _text("total_tokens 是整数不是字符串。")


@handler("ok_resp_len")
def _(body, headers):
    return 200, _text("choices 恰好一个。")


@handler("bad_resp_len")
def _(body, headers):
    return 200, _text("choices 数量与期望不符。")


@handler("ok_resp_contains")
def _(body, headers):
    return 200, _text("答案是 42。")


@handler("bad_resp_contains_ic")
def _(body, headers):
    return 200, _text("model 字段是大写。")


@handler("ok_json_parse")
def _(body, headers):
    return 200, _text('{"ok": true}')


@handler("ok_json_fences")
def _(body, headers):
    return 200, _text('```json\n{"city": "Tokyo", "temp_c": 21}\n```')


@handler("bad_json_fences_prose")
def _(body, headers):
    return 200, _text('结果是：\n```json\n{"city": "Tokyo", "temp_c": 21}\n```\n以上就是结果。')


@handler("bad_json_unparseable")
def _(body, headers):
    return 200, _text("抱歉，这次没法输出合法 JSON。")


@handler("ok_json_fields")
def _(body, headers):
    return 200, _text(json.dumps(
        {"name": "箱子", "age": 30, "tags": ["红", "蓝"]}, ensure_ascii=False))


@handler("bad_json_bool")
def _(body, headers):
    return 200, _text(json.dumps(
        {"name": "箱子", "age": True, "tags": ["红", "蓝"]}, ensure_ascii=False))


@handler("bad_json_item_nonempty")
def _(body, headers):
    return 200, _text(json.dumps(
        {"name": "箱子", "age": 30, "tags": ["红", ""]}, ensure_ascii=False))


@handler("ok_json_in")
def _(body, headers):
    return 200, _text('{"sentiment": "positive"}')


@handler("bad_json_in")
def _(body, headers):
    return 200, _text('{"sentiment": "neutral"}')


@handler("ok_json_neq")
def _(body, headers):
    return 200, _text('{"status": "ok"}')


@handler("bad_json_neq")
def _(body, headers):
    return 200, _text('{"status": "error"}')


@handler("ok_json_contains")
def _(body, headers):
    return 200, _text('{"items": ["widget", "gear", "bolt"]}')


@handler("bad_json_len")
def _(body, headers):
    return 200, _text('{"tags": ["红", "蓝"]}')


@handler("bad_json_list_index")
def _(body, headers):
    return 200, _text('{"items": [{"name": "widget"}]}')


@handler("ok_json_eq_ic")
def _(body, headers):
    return 200, _text('{"user": {"city": "Shanghai"}}')


# --------------------------------------------------------------- 工具域

@handler("ok_tool")
def _(body, headers):
    return 200, _tool("get_weather", {"city": "Tokyo", "unit": "celsius"})


@handler("bad_tool_name")
def _(body, headers):
    return 200, _tool("get_time", {"tz": "Asia/Tokyo"})


@handler("bad_tool_absent")
def _(body, headers):
    return 200, _text("今天天气不错，不需要工具。")


@handler("ok_no_tool")
def _(body, headers):
    return 200, _text("不需要调用工具，我直接回答。")


@handler("bad_no_tool")
def _(body, headers):
    return 200, _tool("get_weather", {"city": "Tokyo"})


@handler("ok_tool_args_nested")
def _(body, headers):
    return 200, _tool("get_weather", {"location": {"city": "Tokyo"}})


@handler("bad_tool_args")
def _(body, headers):
    return 200, _tool("get_weather", {"city": "Paris"})


@handler("bad_tool_args_badjson")
def _(body, headers):
    return 200, _tool("get_weather", "not-a-json{")


# --------------------------------------------------------------- API 契约域

@handler("ok_empty_messages")
def _(body, headers):
    if not body.get("messages"):
        return _err(400, "messages must not be empty")
    return 200, _text("EMPTY_MESSAGES_CHECK_FAILED：messages 不为空")


@handler("ok_bad_auth")
def _(body, headers):
    auth = headers.get("Authorization") or ""
    if auth == "Bearer sk-wrong-key":
        return _err(401, "Invalid API key")
    return 200, _text(f"AUTH_CHECK_FAILED: Authorization={auth!r}")


@handler("ok_no_auth")
def _(body, headers):
    if "Authorization" not in headers:
        return _err(401, "Missing Authorization header")
    return 200, _text("NO_AUTH_CHECK_FAILED: Authorization 仍在")


@handler("ok_omit_messages")
def _(body, headers):
    if "messages" not in body:
        return _err(400, "messages is required")
    return 200, _text("OMIT_CHECK_FAILED: messages 仍在请求体里")


@handler("ok_wire_header")
def _(body, headers):
    trace = headers.get("X-Trace") or "MISSING"
    return 200, _text(f"已收到自定义请求头 X-Trace={trace}")


# --------------------------------------------------------------- 流式域

@handler("ok_stream_all")
def _(body, headers):
    return _sse(["流式", "回复", "逐块", "输出", "完成", "。"])


@handler("bad_stream_chunks")
def _(body, headers):
    return _sse(["只有一块"])


@handler("bad_stream_done")
def _(body, headers):
    return _sse(["正常", "输出"], done=False)


@handler("ok_stream_no_done")
def _(body, headers):
    return _sse(["正常", "输出"], done=False)


@handler("bad_stream_tool")
def _(body, headers):
    tool_delta = {"index": 0, "id": "call_1", "type": "function",
                  "function": {"name": "get_weather",
                               "arguments": json.dumps({"city": "Tokyo"})}}
    return _sse([], finish="tool_calls", done=True, tool_delta=tool_delta)


@handler("ok_stream_resp")
def _(body, headers):
    return _sse(["伪响应", "结构", "可断言"])


# --------------------------------------------------------------- vars 生成器域

@handler("ok_vars_haystack")
def _(body, headers):
    m = re.search(r"The access code is (\S+?)\.", _user_text(body))
    return 200, _text(m.group(1) if m else "NEEDLE_NOT_FOUND")


@handler("ok_vars_seeded")
def _(body, headers):
    return 200, _text("收到：" + _user_text(body))


@handler("ok_vars_left_placeholder")
def _(body, headers):
    m = re.search(r"\{[A-Z][A-Z0-9_]*\}", _user_text(body))
    if m:
        return 200, _text(f"检测到未替换的占位符 {m.group(0)}")
    return 200, _text("LEFT_PLACEHOLDER_CHECK_FAILED：占位符不在了？")


@handler("ok_vars_garbled")
def _(body, headers):
    return 200, _text("收到：" + _user_text(body))


@handler("ok_vars_params")
def _(body, headers):
    return 200, _text(f"user_id={body.get('user_id')}")


# --------------------------------------------------------------- HTTP 壳

class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send_json(*_err(405, "Method Not Allowed"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            # _raw_body 用例（text/plain 原始串）走这里：静态 400 错误体
            self._send_json(*_err(400, "请求体不是合法 JSON（Content-Type: text/plain 不接受）"))
            return
        fn = HANDLERS.get(str(body.get("case")))
        if fn is None:
            self._send_json(*_err(500, f"fake_model: 未知 case {body.get('case')!r}，"
                                       "cases.jsonl 与 fake_model.py 脱同步？"))
            return
        status, payload = fn(body, dict(self.headers))
        if isinstance(payload, dict) and "__sse__" in payload:
            self._send_sse(payload["__sse__"])
        else:
            self._send_json(status, payload)

    def _send_json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_sse(self, script: dict):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        first = True
        for part in script["parts"]:
            delta = {"role": "assistant"} if first else {}
            first = False
            delta["content"] = part
            self._sse_event({"choices": [{"delta": delta}]})
        if script.get("tool_delta"):
            self._sse_event({"choices": [{"delta": {"tool_calls": [script["tool_delta"]]}}]})
        self._sse_event({"choices": [{"delta": {}, "finish_reason": script["finish"]}]})
        if script["done"]:
            self._sse_event("[DONE]")
        self.wfile.flush()

    def _sse_event(self, data):
        payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        self.wfile.write(f"data: {payload}\n\n".encode())

    def log_message(self, *args):  # 安静模式：selfcheck 由外层脚本汇总
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8391)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FakeHandler)
    server.daemon_threads = True
    print(f"fake_model listening on http://127.0.0.1:{args.port}/v1/chat/completions, "
          f"{len(HANDLERS)} case handlers")
    server.serve_forever()


if __name__ == "__main__":
    main()
