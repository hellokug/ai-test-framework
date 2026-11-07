"""功能测试 harness（pytest 插件，经 ``-p aite.functional.harness`` 加载）。

两种用例形态，可在同一场景目录共存：

1. **数据驱动（推荐）**：场景目录放 ``cases.jsonl``，一行 = 一例请求体 + 断言，
   本插件自动收集（``pytest_collect_file``），无需写任何 Python::

       {"id": "intro", "desc": "自我介绍一句话", "messages": [...],
        "expect": {"nonempty": true}}

2. **手写用例**：普通 ``test_*.py``，用 ``chat`` / ``chat_raw`` fixture 访问模型。

行结构（cases.jsonl）：

- ``id``：用例 id（场景内唯一，即测试名）
- ``desc``：场景说明（这个请求体测什么）
- ``messages`` / ``tools``（可选）/ ``params``（可选）：请求体
- ``vars``（可选）：占位符变量，messages 中 ``{NAME}`` 被替换为生成值。生成器：
  ``haystack{chars,key_at,key}``（确定性长文埋码，needle 取回测试）/
  ``random_string{length,charset?,seed?}``（定长随机串，charset ∈ alnum/letters/digits/hex）/
  ``random_number{min?,max?,seed?}``（区间随机整数）/ ``garbled{length,seed?}``（随机乱码）/
  ``random_choice{options,seed?}``（随机选一项）；``seed`` 缺省 = 每次执行取新随机值
  （同一次执行的发送与记录保持一致），给定 = 可复现。占位符可出现在 **messages 与
  params** 两处（input 等自定义顶层字段的低代码生成值），同一次执行内同占位符同值
- ``expect``（必填，至少一个断言）：
  ``contains`` / ``ignore_case`` / ``nonempty`` /
  ``json{path,type,eq,neq,in,len,contains,nonempty,item_type,item_nonempty,fields}``（空规格
  ``{}`` = 仅校验剥围栏后可解析，即 JSON 输出格式正确性）/
  ``tool`` / ``tool_args_contains``，以及质量类断言：
  ``no_garbled``（乱码）/ ``no_leak``（内部标签泄露，可 ``{extra_patterns}`` 扩展）/
  ``no_repeat``（复读）；API 契约类断言：
  ``not_contains`` / ``no_tool``（不发起 tool call）/ ``http_status``（预期错误码；
  可关系形式 ``{"neq"/"gte"/"lte"/"in": 值}``，如 ``{"neq": 200}``/``{"gte": 500}``，
  配 ``params`` 发坏请求测参数校验）/ ``finish_reason`` /
  ``resp{path,type,eq,len,nonempty}``（响应体字段断言，如 usage.id 等）。
  ``stream: true`` 经 ``params`` 传入即走 SSE 流式（断言拼接后的完整结果）。

模型连接信息从环境变量读取（由 pytest 适配器注入，不要直接 pytest 场景目录）：
``AITE_FT_BASE_URL`` / ``AITE_FT_MODEL`` / ``AITE_FT_API_KEY``（本地服务可缺省）。
网络错误 / 5xx 统一重试 1 次；语义断言不重试；4xx 配置类错误直接抛出——
除非用例配了 ``http_status``（预期错误模式：错误响应本身是被测对象，不重试不抛）。
环境变量缺失在**请求时**才失败（pytest.fail 带指引），收集不需要任何环境。
"""

from __future__ import annotations

import copy
import json
import os
import random
import re
from collections import Counter

import httpx
import pytest

RETRYABLE_TIMEOUT = 120.0   # 单请求超时（本地小模型推理可能较慢）
_COMPLETIONS = "/chat/completions"
CASES_FILENAME = "cases.jsonl"

_TYPES = {"str": str, "int": int, "list": list, "dict": dict}
_EXPECT_KEYS = {"contains", "ignore_case", "nonempty", "json", "tool",
                "tool_args_contains", "no_garbled", "no_leak", "no_repeat",
                "not_contains", "no_tool", "http_status", "finish_reason", "resp",
                "chunks_min", "sse_done"}
_JSON_SPEC_KEYS = {"path", "type", "eq", "neq", "in", "len", "contains", "nonempty",
                   "item_type", "item_nonempty", "fields"}
_RESP_SPEC_KEYS = {"path", "type", "eq", "len", "nonempty", "contains"}   # 响应体断言（无递归 fields）
_STATUS_OPS = {"eq": "==", "neq": "!=", "gte": ">=", "lte": "<=", "in": "∈"}   # http_status 关系形式
_VAR_BUILDERS = {"haystack", "random_string", "random_number", "garbled", "random_choice"}
_VAR_PARAM_KEYS = {"haystack": {"chars", "key_at", "key"},
                   "random_string": {"length"},
                   "garbled": {"length"},
                   "random_choice": {"options"},
                   "random_number": set()}   # min/max/seed 全缺省
_STRING_CHARSETS = {"alnum": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                    "letters": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
                    "digits": "0123456789",
                    "hex": "0123456789abcdef"}
# 乱码采样区段：mojibake 拉丁段 + 多语区段 + 制表符号 + 私用区（豆腐块观感）。
# 刻意排除代理区（U+D800-DFFF）与控制字符——产物可安全 JSON 序列化与落库展示。
_GARBLED_RANGES = [(0xA1, 0xFF), (0x3000, 0x303F), (0x4E00, 0x9FFF), (0x0400, 0x04FF),
                   (0x0370, 0x03FF), (0x2500, 0x257F), (0xE000, 0xF8FF)]
_FINISH_REASONS = {"stop", "length", "tool_calls", "content_filter"}

_REPLY_BRIEF = 160   # 失败信息里实际回复的截断长度
_INPUT_EDGE = 200    # 失败详情：输入每条消息头/尾各保留的字符数（长文 16k 等截断）
_OUTPUT_BRIEF = 300  # 失败详情：输出保留的字符数
_ARGS_BRIEF = 120    # 失败详情：tool_call arguments 保留的字符数


# ---------------------------------------------------------------- 模型访问层

def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.fail(
            f"环境变量 {name} 未设置。功能测试需由 pytest 适配器注入模型连接信息："
            "请通过 `aite run -c <suite.yaml>`（engine: pytest）运行，"
            "不要直接 pytest 场景目录。"
        )
    return value


def _request_url() -> str:
    """base_url → 完整 chat/completions URL（复用平台 target 解析口径）。"""
    base = _required_env("AITE_FT_BASE_URL").rstrip("/")
    return base if base.endswith(_COMPLETIONS) else base + _COMPLETIONS


def _post(client: httpx.Client, messages: list, *, tools: list | None,
          params: dict, expect_error: bool = False) -> dict:
    """发 chat/completions 请求。

    - ``stream: true`` 走 SSE 流式：逐块拼接，返回伪响应（finish_reason 取末块），
      额外带 ``_chunks``（块数）与 ``_sse_ok``（data: [DONE] 正常收尾）
    - ``expect_error=True``（用例配了 http_status）：错误响应是被测对象，
      不重试、不 raise，原样返回 ``{"_status": 码, "_body": ...}``
    - 请求构造控制（params 下划线前缀，不进 payload）：``_omit_messages``
      （省略 messages 字段）、``_http_method``（改用 GET 等）、``_raw_body``
      （以 text/plain 发原始字符串）
    """
    url = _request_url()
    ctl = {k: params[k] for k in params if str(k).startswith("_")}
    payload: dict = {
        "model": _required_env("AITE_FT_MODEL"),
        **({"messages": messages} if not ctl.get("_omit_messages") else {}),
        **{k: v for k, v in params.items() if not str(k).startswith("_")},
    }
    if tools:
        payload["tools"] = tools
    headers = {"Authorization": f"Bearer {os.environ.get('AITE_FT_API_KEY') or 'EMPTY'}"}
    if ctl.get("_http_method") == "GET":
        resp = client.get(url, headers=headers)
        return {"_status": resp.status_code, "_body": _safe_json(resp)}
    if "_raw_body" in ctl:
        resp = client.post(url, content=str(ctl["_raw_body"]),
                           headers={**headers, "Content-Type": "text/plain"})
        return {"_status": resp.status_code, "_body": _safe_json(resp)}
    if payload.get("stream"):
        return _post_stream(client, url, payload, headers)
    last_error = ""
    for attempt in (1, 2):
        try:
            resp = client.post(url, json=payload, headers=headers)
        except httpx.TransportError as exc:   # 连接失败 / 超时 → 重试 1 次
            last_error = f"transport error: {exc}"
            continue
        if resp.status_code >= 500 and attempt == 1:
            last_error = f"server error {resp.status_code}"
            continue
        if expect_error:                      # 预期错误模式：原样返回不抛
            return {"_status": resp.status_code, "_body": _safe_json(resp)}
        if resp.status_code >= 400:
            resp.raise_for_status()           # 非预期的 4xx/5xx（已重试）直接抛出
        return resp.json()
    if expect_error:   # 两次都没拿到响应（网络错误）→ 预期错误模式下按最终态返回
        raise AssertionError(f"预期收到 HTTP 错误码，实际网络失败：{last_error}")
    pytest.fail(f"模型服务请求失败（已重试 1 次）：{last_error}")  # pragma: no cover


def _safe_json(resp) -> object:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:200]


def _post_stream(client: httpx.Client, url: str, payload: dict,
                 headers: dict) -> dict:
    """SSE 流式请求：拼接 delta → 伪响应 dict（不重试，流式重试语义复杂）。"""
    try:
        with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code >= 400:       # 流式下的错误响应（非 SSE）
                resp.read()
                return {"_status": resp.status_code, "_body": _safe_json(resp)}
            content_parts: list[str] = []
            finish_reason = None
            role = None
            n_chunks = 0
            done = False
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[len("data: "):].strip()
                if data == "[DONE]":
                    done = True
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                n_chunks += 1
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("role"):
                        role = delta["role"]
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                    if ch.get("finish_reason"):
                        finish_reason = ch["finish_reason"]
            return {
                "choices": [{
                    "message": {"role": role or "assistant",
                                "content": "".join(content_parts)},
                    "finish_reason": finish_reason,
                }],
                "_chunks": n_chunks,
                "_sse_ok": done,
            }
    except httpx.TransportError as exc:
        pytest.fail(f"流式请求失败：{exc}")


_CLIENT: httpx.Client | None = None


def _shared_client() -> httpx.Client:
    """进程级共享 client（fixture 与 cases.jsonl 收集的 Item 共用）。"""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(timeout=RETRYABLE_TIMEOUT)
    return _CLIENT


def strip_fences(text: str) -> str:
    """剥离 Markdown 代码围栏（```json ... ``` → 内容）。"""
    t = text.strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


@pytest.fixture(scope="session")
def _client():
    yield _shared_client()


@pytest.fixture()
def chat(_client: httpx.Client):
    """messages → 助手文本回复（str）。手写用例用。"""
    def _chat(messages: list, *, tools: list | None = None, **params) -> str:
        data = _post(_client, messages, tools=tools, params=params)
        return (data["choices"][0]["message"].get("content") or "").strip()
    return _chat


@pytest.fixture()
def chat_raw(_client: httpx.Client):
    """messages → 完整响应 dict（断言 tool_calls 等原始结构时用）。"""
    def _raw(messages: list, *, tools: list | None = None, **params) -> dict:
        return _post(_client, messages, tools=tools, params=params)
    return _raw


# ---------------------------------------------------------------- 数据驱动

# 质量类断言（保守口径：宁可漏报不误报，正常文本一律通过）
_GARBLED_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("替换字符 U+FFFD", re.compile("\ufffd")),
    ("GBK 乱码『锟斤拷』", re.compile("锟斤拷")),
    ("控制字符", re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u0080-\u009f]")),
    # UTF-8 按 Latin-1 误解码特征（如 中文 → "æµ‹è¯•"）；正常重音字符（château）不命中
    ("UTF-8/Latin-1 误解码", re.compile(r"[\u00c0-\u00ff][\u0080-\u00bf]")),
]

_LEAK_DEFAULT = re.compile("|".join([
    r"<\|[^|>\n]{1,30}\|>",                   # ChatML/Qwen: <|im_start|> <|endoftext|>
    r"<｜[^｜>\n]{1,30}｜>",                  # DeepSeek 全角变体
    r"\[/?INST\]",                            # Mistral
    r"<</?SYS>>",                             # Llama-2
    r"</?(?:think|tool_call|function_call)>",  # 推理/工具标签泄露
]))

_REPEAT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(.{6,40}?)\1{3,}", re.S), "连续复读（≥6 字符块 ×4）"),
    (re.compile(r"(.{2,5}?)\1{9,}", re.S), "高频短块复读（×10）"),
]


def _garbled_evidence(text: str) -> str | None:
    for name, rx in _GARBLED_PATTERNS:
        hits = rx.findall(text)
        if hits:
            return f"{name} ×{len(hits)}"
    return None


def _leak_evidence(text: str, extra_patterns: list[re.Pattern]) -> str | None:
    m = _LEAK_DEFAULT.search(text)
    if m:
        return m.group(0)
    for rx in extra_patterns:
        m = rx.search(text)
        if m:
            return m.group(0)
    return None


def _repeat_evidence(text: str) -> str | None:
    for rx, desc in _REPEAT_PATTERNS:
        m = rx.search(text)
        if m:
            block = m.group(1)
            return f"{desc}: {block!r}"
    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 10]
    if lines:
        line, n = Counter(lines).most_common(1)[0]
        if n >= 5:
            return f"整行重复 ×{n}: {line[:40]!r}"
    return None


def build_haystack(chars: int, key_at: float, key: str) -> str:
    """确定性长文填充：chars 量级重复无意义句，key 句埋在 key_at 比例处。"""
    filler = "The quiet train crosses the long bridge every morning before sunrise.\n"
    doc = filler * (int(chars) // len(filler))
    idx = int(len(doc) * float(key_at))
    return doc[:idx] + f"\nThe access code is {key}.\n" + doc[idx:]


def build_random_string(*, length: int, charset: str = "alnum", seed: int | None = None) -> str:
    """定长随机字符串；charset ∈ alnum/letters/digits/hex；seed 缺省每次执行取新值。"""
    pool = _STRING_CHARSETS.get(charset)
    if not pool:
        raise ValueError(f"未知字符集 {charset!r}（可用: {sorted(_STRING_CHARSETS)}）")
    if not isinstance(length, int) or isinstance(length, bool) or length <= 0:
        raise ValueError(f"length 须为正整数，实际: {length!r}")
    rng = random.Random(seed)
    return "".join(rng.choice(pool) for _ in range(length))


def build_random_number(*, min: int = 0, max: int = 100, seed: int | None = None) -> str:
    """[min, max] 区间随机整数（含端点），以字符串形式插入占位符。"""
    lo, hi = int(min), int(max)
    if lo > hi:
        raise ValueError(f"min 不得大于 max（实际 {lo} > {hi}）")
    return str(random.Random(seed).randint(lo, hi))


def build_garbled(*, length: int, seed: int | None = None) -> str:
    """随机乱码（编码鲁棒性测试）：mojibake 底料 + 多区段 Unicode 采样。"""
    if not isinstance(length, int) or isinstance(length, bool) or length <= 0:
        raise ValueError(f"length 须为正整数，实际: {length!r}")
    rng = random.Random(seed)
    mojibake = "功能用例样例 mixed sample 0123".encode().decode("latin-1")   # utf-8 字节按 latin-1 解 = 乱码底料
    chars = [mojibake[rng.randrange(len(mojibake))]]
    while len(chars) < length:
        lo, hi = rng.choice(_GARBLED_RANGES)
        chars.append(chr(rng.randint(lo, hi)))
    return "".join(chars[:length])


def build_random_choice(*, options: list, seed: int | None = None) -> str:
    """从 options 随机选一项（参数化 / 等价类测试）。"""
    if not isinstance(options, list) or not options:
        raise ValueError("options 须为非空数组")
    return str(random.Random(seed).choice(options))


def _build_var(spec: dict) -> str:
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"vars 项必须是单键对象（如 {{\"haystack\": {{...}}}}），实际: {spec!r}")
    (kind, kw), = spec.items()
    if kind not in _VAR_BUILDERS:
        raise ValueError(f"未知 vars 生成器 {kind!r}（可用: {sorted(_VAR_BUILDERS)}）")
    if not isinstance(kw, dict):
        raise ValueError(f"vars 生成器 {kind} 的参数须为对象，实际: {kw!r}")
    missing = _VAR_PARAM_KEYS[kind] - set(kw)
    if missing:
        raise ValueError(f"vars 生成器 {kind} 缺少参数 {sorted(missing)}")
    seed = kw.get("seed")
    if kind == "haystack":
        return build_haystack(kw["chars"], kw["key_at"], str(kw["key"]))
    if kind == "random_string":
        return build_random_string(length=kw["length"], charset=kw.get("charset", "alnum"), seed=seed)
    if kind == "random_number":
        return build_random_number(min=kw.get("min", 0), max=kw.get("max", 100), seed=seed)
    if kind == "garbled":
        return build_garbled(length=kw["length"], seed=seed)
    return build_random_choice(options=kw["options"], seed=seed)


def _brief(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= _REPLY_BRIEF else text[:_REPLY_BRIEF] + "…"


def _ellipsize(text: str, edge: int) -> str:
    """压平空白后头尾各留 edge 字符，超长中段标注总长（失败详情的体积预算）。"""
    flat = " ".join(str(text).split())
    if len(flat) <= edge * 2:
        return flat
    return flat[:edge] + f" ……（共 {len(flat)} 字符，中段省略）…… " + flat[-edge:]


def failure_detail(case: Case, response: dict | None, exc: AssertionError) -> str:
    """断言失败详情：断言 → 输入 → 输出（逐项截断，控制日志/junit/CLI 体积）。

    进入 pytest 的 repr_failure / junit ``<failure>`` / 场景日志 / CLI 汇总，
    每失败例预算 < 1KB；16k haystack 之类长输入按头尾截断并标注总长。
    """
    # 只取首行：pytest 断言重写会给 str(exc) 追加 "assert False" 之类的解释行，
    # 我们的断言消息本身都是单行构造
    msg = str(exc).splitlines() or [""]
    lines = [f"[{case.id}] {case.desc}", f"  断言失败: {msg[0]}"]
    try:
        msgs = case.rendered_messages()
    except Exception:                              # noqa: BLE001 - 详情组装不掩盖原断言
        msgs = case.messages
    parts = [f"{m.get('role')}: {_ellipsize(str(m.get('content', '')), _INPUT_EDGE)}"
             for m in msgs[:6]]
    lines.append("  输入 | " + " || ".join(parts))
    if response is not None:
        if "_status" in response:   # 预期错误模式：呈现状态码与错误体
            lines.append(f"  输出 | HTTP {response['_status']} "
                         f"{_ellipsize(str(response.get('_body')), _OUTPUT_BRIEF // 2)}")
            return "\n".join(lines)
        choices = response.get("choices") or []
        message = (choices[0] or {}).get("message") or {} if choices else {}
        content = (message.get("content") or "").strip()
        tool_calls = message.get("tool_calls") or []
        if response.get("_chunks") is not None:   # 流式：附 chunk 数与收尾状态
            lines.append(f"  输出 | stream: {response['_chunks']} chunks, "
                         f"sse_done={response['_sse_ok']}")
        if content:
            lines.append(f"  输出 | {_ellipsize(content, _OUTPUT_BRIEF // 2)}")
        if tool_calls:
            fn = (tool_calls[0].get("function") or {})
            lines.append(f"  输出 | tool_call: {fn.get('name')}"
                         f"({_ellipsize(str(fn.get('arguments')), _ARGS_BRIEF // 2)})")
        if not content and not tool_calls:
            lines.append("  输出 | <空>")
    return "\n".join(lines)


def _validate_json_spec(spec: dict, where: str) -> None:
    if not isinstance(spec, dict):
        raise ValueError(f"{where}: json 断言必须是对象")
    unknown = set(spec) - _JSON_SPEC_KEYS
    if unknown:
        raise ValueError(f"{where}: 未知 json 断言键 {sorted(unknown)}（可用: {sorted(_JSON_SPEC_KEYS)}）")
    for key in ("type", "item_type"):
        if key in spec and spec[key] not in _TYPES:
            raise ValueError(f"{where}: 未知类型 {spec[key]!r}（可用: {sorted(_TYPES)}）")
    for fname, sub in (spec.get("fields") or {}).items():
        _validate_json_spec(sub, f"{where}.fields.{fname}")


def _validate_status(hs, source: str) -> None:
    """http_status：int（== 语义，兼容）或单键 {op: 值} 对象，op ∈ eq/neq/gte/lte/in。"""
    if hs is None:
        return
    if isinstance(hs, dict):
        if len(hs) != 1 or next(iter(hs), None) not in _STATUS_OPS:
            raise ValueError(f"{source}: http_status 关系须为单键对象 {sorted(_STATUS_OPS)}")
        op, val = next(iter(hs.items()))
        vals = val if op == "in" else [val]
        if not isinstance(vals, list) or not vals or not all(
                isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599 for v in vals):
            raise ValueError(f"{source}: http_status {_STATUS_OPS[op]} 的值须为 100-599 的整数"
                             + ("，逗号分隔多值" if op == "in" else ""))
        return
    if not isinstance(hs, int) or isinstance(hs, bool) or not 100 <= hs <= 599:
        raise ValueError(f"{source}: http_status 须为 100-599 的整数"
                         f"或 {sorted(_STATUS_OPS)} 关系对象")


def _type_ok(value, type_name: str) -> bool:
    if type_name == "int":   # bool 是 int 子类，须排除
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, _TYPES[type_name])


def _check_json_spec(spec: dict, value, where: str, ic: bool) -> None:
    if "path" in spec:
        path = str(spec["path"])
        for part in path.split("."):
            value = value[part] if isinstance(value, dict) and part in value else _missing(
                where, path, part, value)
        where = f"{where}.{path}"
    if "type" in spec:
        assert _type_ok(value, spec["type"]), (
            f"{where}: 期望类型 {spec['type']}，实际 {type(value).__name__}（{_brief(value)}）")
    if "len" in spec:
        assert isinstance(value, (list, str, dict)) and len(value) == spec["len"], (
            f"{where}: 期望长度 {spec['len']}，实际 {len(value) if hasattr(value, '__len__') else value!r}")
    if spec.get("nonempty"):
        assert isinstance(value, str) and value.strip(), f"{where}: 期望非空字符串"
    if "eq" in spec:
        expected = spec["eq"]
        got = value
        if ic and isinstance(value, str) and isinstance(expected, str):
            got, expected = value.strip().lower(), expected.strip().lower()
        assert got == expected, f"{where}: 期望 == {expected!r}，实际 {_brief(value)}"
    if "neq" in spec:      # 不等关系恒区分大小写（与 eq 的 ic 语义刻意不同，避免双重歧义）
        assert value != spec["neq"], (
            f"{where}: 期望 != {spec['neq']!r}，实际与之相等 {_brief(value)}")
    if "contains" in spec:   # like：目标转字符串后做子串包含
        needle = str(spec["contains"])
        hay = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if ic:
            assert needle.lower() in hay.lower(), (
                f"{where}: 期望包含 {needle!r}，实际 {_brief(value)}")
        else:
            assert needle in hay, f"{where}: 期望包含 {needle!r}，实际 {_brief(value)}"
    if "in" in spec:
        options = spec["in"]
        got = value.strip().lower() if ic and isinstance(value, str) else value
        hit = any(got == (o.strip().lower() if ic and isinstance(o, str) else o)
                  for o in options)
        assert hit, f"{where}: 期望 ∈ {options!r}，实际 {_brief(value)}"
    if "item_type" in spec:
        assert isinstance(value, list), f"{where}: item_type 仅适用于 list"
        for i, item in enumerate(value):
            assert _type_ok(item, spec["item_type"]), (
                f"{where}[{i}]: 期望元素类型 {spec['item_type']}，"
                f"实际 {type(item).__name__}（{_brief(item)}）")
    if spec.get("item_nonempty"):
        for i, item in enumerate(value):
            assert isinstance(item, str) and item.strip(), f"{where}[{i}]: 期望非空字符串元素"
    for fname, sub in (spec.get("fields") or {}).items():
        assert isinstance(value, dict) and fname in value, (
            f"{where}: 缺字段 {fname!r}（实际键: {sorted(value) if isinstance(value, dict) else value!r}）")
        _check_json_spec(sub, value[fname], f"{where}.{fname}", ic)


def _missing(where: str, path: str, part: str, value) -> None:
    raise AssertionError(f"{where}: 路径 {path!r} 在 {part!r} 处不存在（实际 {_brief(value)}）")


class Case:
    """cases.jsonl 的一行：请求体 + 断言（数据驱动用例）。"""

    def __init__(self, raw: dict, source: str):
        missing = [k for k in ("id", "desc", "messages", "expect") if k not in raw]
        if not raw.get("id"):
            missing.append("id")
        if not raw.get("desc"):
            missing.append("desc")
        if missing:
            missing = sorted(set(missing))
            raise ValueError(f"{source}: 缺少必填字段 {missing}（id/desc/messages/expect）")
        # messages 允许空数组：参数校验用例（empty_messages → 400）需要发空数组
        expect = raw["expect"]
        if not isinstance(expect, dict):
            raise ValueError(f"{source}: expect 必须是对象")
        unknown = set(expect) - _EXPECT_KEYS
        if unknown:
            raise ValueError(f"{source}: 未知断言键 {sorted(unknown)}（可用: {sorted(_EXPECT_KEYS)}）")
        _validate_json_spec(expect.get("json") or {}, f"{source}: json")
        _validate_status(expect.get("http_status"), source)
        cm = expect.get("chunks_min")
        if cm is not None and (not isinstance(cm, int) or cm < 1):
            raise ValueError(f"{source}: chunks_min 须为 ≥1 的整数")
        if "sse_done" in expect and not isinstance(expect["sse_done"], bool):
            raise ValueError(f"{source}: sse_done 须为布尔值")
        fr = expect.get("finish_reason")
        if fr is not None and fr not in _FINISH_REASONS:
            raise ValueError(f"{source}: finish_reason 须 ∈ {sorted(_FINISH_REASONS)}")
        rsp = expect.get("resp")
        if rsp is not None:
            specs = rsp if isinstance(rsp, list) else [rsp]   # 数组 = 一条用例断言多个字段
            if not specs or not all(isinstance(s, dict) for s in specs):
                raise ValueError(f"{source}: resp 须为对象或对象数组")
            for i, spec in enumerate(specs):
                unknown = set(spec) - _RESP_SPEC_KEYS
                if unknown:
                    raise ValueError(
                        f"{source}: resp[{i}] 未知键 {sorted(unknown)}（可用: {sorted(_RESP_SPEC_KEYS)}）")
                if spec.get("type") is not None and spec["type"] not in _TYPES:
                    raise ValueError(f"{source}: resp[{i}].type 须 ∈ {sorted(_TYPES)}")
                if not any(k in spec for k in _RESP_SPEC_KEYS):
                    raise ValueError(f"{source}: resp[{i}] 至少含一个断言键")
        if "no_leak" in expect:
            leak = expect["no_leak"]
            if leak is True:
                extra: list[str] = []
            elif isinstance(leak, dict):
                unknown = set(leak) - {"extra_patterns"}
                if unknown:
                    raise ValueError(f"{source}: no_leak 未知配置 {sorted(unknown)}")
                eps = leak.get("extra_patterns") or []
                if not isinstance(eps, list) or not all(
                        isinstance(p, str) and p for p in eps):
                    raise ValueError(f"{source}: no_leak.extra_patterns 须为非空正则字符串列表")
                try:
                    for p in eps:
                        re.compile(p)
                except re.error as exc:
                    raise ValueError(f"{source}: no_leak.extra_patterns 非法正则: {exc}") from None
                extra = eps
            else:
                raise ValueError(f"{source}: no_leak 只接受 true 或 {{extra_patterns: [...]}}")
        else:
            extra = []
        self.id = str(raw["id"])
        self.desc = str(raw["desc"])
        self.messages = raw["messages"]
        self.tools = raw.get("tools")
        self.params: dict = raw.get("params") or {}
        self.vars: dict = raw.get("vars") or {}
        self.expect = expect
        self._extra_leak = [re.compile(p) for p in extra]
        self._rendered: list | None = None       # messages 渲染缓存
        self._rendered_params: dict | None = None  # params 渲染缓存
        self._values: dict | None = None         # 同一次执行内各字段共享同一组生成值
        for spec in self.vars.values():
            _build_var(spec)   # 加载期校验生成器规格

    def _render_values(self) -> dict:
        """生成值（实例级缓存）：messages 与 params 共享同一组，{g1} 两处同值。"""
        if self._values is None:
            self._values = {name: _build_var(spec) for name, spec in self.vars.items()}
        return self._values

    @staticmethod
    def _subst(values: dict, obj):
        def _replace(o):
            if isinstance(o, str):
                for name, val in values.items():
                    o = o.replace("{" + name + "}", val)
                return o
            if isinstance(o, list):
                return [_replace(x) for x in o]
            if isinstance(o, dict):
                return {k: _replace(v) for k, v in o.items()}
            return o
        return _replace(obj)

    def rendered_messages(self) -> list:
        """应用 vars 占位符后的 messages（{NAME} → 生成值）。

        实例级渲染缓存：随机生成器（seed 缺省）在发送（_post）与结果记录
        两处调用间返回同一份，保证「记录的输入 = 实际发送的输入」；
        跨执行（重新采集/重跑）取新随机值。
        """
        if not self.vars:
            return self.messages
        if self._rendered is None:
            self._rendered = self._subst(self._render_values(),
                                         copy.deepcopy(self.messages))
        return self._rendered

    def rendered_params(self) -> dict:
        """应用 vars 占位符后的 params（input 等自定义顶层字段的低代码生成值）。"""
        if not self.vars or not self.params:
            return self.params
        if self._rendered_params is None:
            self._rendered_params = self._subst(self._render_values(),
                                                copy.deepcopy(self.params))
        return self._rendered_params

    def check(self, response: dict) -> None:
        """按 expect 校验响应（失败抛 AssertionError，信息含期望与实际）。

        断言键为封闭集合（校验期白名单），按域分组顺序执行、可叠加：
        内容质量 → 结构/流式 → 工具行为。
        """
        if "_status" in response:   # 预期错误模式：只跑 http_status（和 resp）断言
            self._check_error_response(response)
            return
        choices = response.get("choices") or []
        choice0: dict = (choices[0] or {}) if choices else {}
        message: dict = choice0.get("message") or {}
        content = (message.get("content") or "").strip()
        self._check_content(content)
        self._check_shape(response, choice0, content)
        tool_calls = message.get("tool_calls") or []
        first_tool = (tool_calls[0].get("function") or {}) if tool_calls else None
        self._check_tool(first_tool)

    def _check_content(self, content: str) -> None:
        """内容域断言：非空/乱码/泄露/复读/包含。"""
        expect = self.expect
        ic = bool(expect.get("ignore_case"))
        if expect.get("nonempty"):
            assert content, "期望回复非空，实际为空"
        if expect.get("no_garbled"):
            bad = _garbled_evidence(content)
            assert bad is None, f"期望无乱码，检测到 {bad}（回复: {_brief(content)}）"
        if "no_leak" in expect:
            hit = _leak_evidence(content, self._extra_leak)
            assert hit is None, f"期望无内部标签泄露，检测到 {hit!r}（回复: {_brief(content)}）"
        if expect.get("no_repeat"):
            ev = _repeat_evidence(content)
            assert ev is None, f"期望无复读，检测到 {ev}（回复: {_brief(content)}）"
        if "contains" in expect:
            needle = str(expect["contains"])
            hit = (needle.lower() in content.lower()) if ic else (needle in content)
            assert hit, f"期望回复包含 {needle!r}，实际: {_brief(content)}"
        if "not_contains" in expect:
            needle = str(expect["not_contains"])
            hit = (needle.lower() in content.lower()) if ic else (needle in content)
            assert not hit, f"期望回复不包含 {needle!r}，实际: {_brief(content)}"

    def _check_shape(self, response: dict, choice0: dict, content: str) -> None:
        """结构域断言：finish_reason/流式分块/SSE 收尾/响应体/JSON。"""
        expect = self.expect
        if "finish_reason" in expect:
            actual = choice0.get("finish_reason")
            assert actual == expect["finish_reason"], (
                f"期望 finish_reason={expect['finish_reason']!r}，实际 {actual!r}")
        if "chunks_min" in expect:
            n = response.get("_chunks") or 0
            assert n >= expect["chunks_min"], (
                f"期望流式 chunk 数 ≥ {expect['chunks_min']}，实际 {n}"
                "（非流式响应或未分块？）")
        if "sse_done" in expect:
            assert response.get("_sse_ok") is expect["sse_done"], (
                f"期望 SSE 收尾 data: [DONE]={expect['sse_done']}，"
                f"实际 {response.get('_sse_ok')}")
        if "resp" in expect:
            specs = expect["resp"]
            for spec in (specs if isinstance(specs, list) else [specs]):
                self._check_resp_spec(spec, response, "resp")
        if "json" in expect:
            try:
                data = json.loads(strip_fences(content))
            except ValueError as exc:
                raise AssertionError(
                    f"期望合法 JSON，解析失败: {exc}；实际: {_brief(content)}") from None
            _check_json_spec(expect["json"], data, "json",
                             bool(expect.get("ignore_case")))

    def _check_tool(self, first_tool: dict | None) -> None:
        """工具域断言：no_tool / tool 选择 / tool arguments 包含。"""
        expect = self.expect
        if "no_tool" in expect:
            assert not first_tool, "期望不发起 tool call，实际有 tool call"
        if "tool" in expect:
            assert first_tool is not None, "期望发起 tool call，实际没有"
            assert first_tool.get("name") == expect["tool"], (
                f"期望选择工具 {expect['tool']!r}，实际 {first_tool.get('name')!r}")
        if "tool_args_contains" in expect:
            assert first_tool is not None, "期望发起 tool call，实际没有"
            try:
                args = json.loads((first_tool or {}).get("arguments") or "")
            except ValueError as exc:
                raise AssertionError(f"tool call arguments 不是合法 JSON: {exc}") from None
            dump = json.dumps(args, ensure_ascii=False).lower()
            needle = str(expect["tool_args_contains"]).lower()
            assert needle in dump, (
                f"期望 tool arguments 包含 {needle!r}，实际: {_brief(dump)}")

    def _check_error_response(self, response: dict) -> None:
        """预期错误模式（响应带 _status）：断言 http_status；resp 断言 _body。"""
        assert "http_status" in self.expect, (
            f"收到错误响应 {response['_status']} 但未配置 http_status 期望")
        expected = self.expect["http_status"]
        actual = response["_status"]
        if isinstance(expected, dict):     # 关系形式 {op: 值}（校验期保证单键合法）
            op, val = next(iter(expected.items()))
            if op == "eq":
                ok = actual == val
            elif op == "neq":
                ok = actual != val
            elif op == "gte":
                ok = actual >= val
            elif op == "lte":
                ok = actual <= val
            else:
                ok = actual in val
            assert ok, (f"期望 HTTP {_STATUS_OPS[op]} {val}，实际 {actual}"
                        f"（body: {_brief(response.get('_body'))}）")
        else:
            assert actual == expected, (
                f"期望 HTTP {expected}，实际 {actual}"
                f"（body: {_brief(response.get('_body'))}）")
        if "resp" in self.expect:
            specs = self.expect["resp"]
            for spec in (specs if isinstance(specs, list) else [specs]):
                self._check_resp_spec(spec, response.get("_body"), "resp",
                              ic=bool(self.expect.get("ignore_case")))

    def _check_resp_spec(self, spec: dict, data, where: str, ic: bool = False) -> None:
        """响应体字段断言（path 支持数字索引如 choices.0；eq 可比较整数）。"""
        if "path" in spec:
            for part in str(spec["path"]).split("."):
                if isinstance(data, list) and part.lstrip("-").isdigit():
                    idx = int(part)
                    assert -len(data) <= idx < len(data), (
                        f"{where}: 路径 {spec['path']!r} 索引 {part} 越界"
                        f"（实际长度 {len(data)}）")
                    data = data[idx]
                else:
                    assert isinstance(data, dict) and part in data, (
                        f"{where}: 路径 {spec['path']!r} 在 {part!r} 处缺失"
                        f"（实际: {_brief(data)}）")
                    data = data[part]
            where = f"{where}.{spec['path']}"
        if "type" in spec:
            assert _type_ok(data, spec["type"]), (
                f"{where}: 期望类型 {spec['type']}，实际 {type(data).__name__}"
                f"（{_brief(data)}）")
        if "len" in spec:
            assert isinstance(data, (list, str, dict)) and len(data) == spec["len"], (
                f"{where}: 期望长度 {spec['len']}，实际 {len(data) if hasattr(data, '__len__') else data!r}")
        if spec.get("nonempty"):
            ok = isinstance(data, (str, list, dict)) and len(data) > 0
            if isinstance(data, str):
                ok = data.strip() != ""
            assert ok, f"{where}: 期望非空（实际: {_brief(data)}）"
        if "contains" in spec:
            needle = str(spec["contains"])
            hay = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            if ic:
                hay, needle = hay.lower(), needle.lower()
            assert needle in hay, f"{where}: 期望包含 {needle!r}，实际 {_brief(data)}"
        if "eq" in spec:
            expected = spec["eq"]
            got = data.strip().lower() if isinstance(data, str) and isinstance(expected, str) \
                and self.expect.get("ignore_case") else data
            assert got == expected, f"{where}: 期望 == {expected!r}，实际 {_brief(data)}"


def load_cases(path) -> list[Case]:
    """加载 cases.jsonl → Case 列表（校验必填项 / id 唯一 / 断言键合法）。"""
    cases: list[Case] = []
    seen: dict[str, int] = {}
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            source = f"{path.name}:{lineno}"
            try:
                raw = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{source}: 行不是合法 JSON: {exc}") from None
            if not isinstance(raw, dict):
                raise ValueError(f"{source}: 行必须是 JSON 对象")
            case = Case(raw, source)
            if case.id in seen:
                raise ValueError(f"{source}: id {case.id!r} 与第 {seen[case.id]} 行重复")
            seen[case.id] = lineno
            cases.append(case)
    return cases


# ------------------------------------------------- pytest 收集（cases.jsonl）

def pytest_collect_file(file_path, parent):
    if file_path.name == CASES_FILENAME:
        return CasesFile.from_parent(parent, path=file_path)
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """用例执行结束即落原始记录（含失败详情）；非 call 阶段（setup/teardown）不记。"""
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call" or getattr(item, "case", None) is None:
        return
    longrepr = rep.longreprtext if rep.failed else None
    record = getattr(item, "record_result", None)
    if record is not None:
        record(rep.outcome, longrepr)


class CasesFile(pytest.File):
    def collect(self):
        for case in load_cases(self.path):
            yield CaseItem.from_parent(self, name=case.id, case=case)


class CaseItem(pytest.Item):
    def __init__(self, *, case: Case, **kwargs):
        super().__init__(**kwargs)
        self.case = case
        self._last_response: dict | None = None

    def runtest(self):
        response = _post(_shared_client(), self.case.rendered_messages(),
                         tools=self.case.tools, params=self.case.rendered_params(),
                         expect_error="http_status" in self.case.expect)
        self._last_response = response   # repr_failure 组装失败详情用（断言→输入→输出）
        try:
            self.case.check(response)
        except AssertionError as exc:
            # 直接抛 Failed 并断开异常链（from None）：不带裸 assert 回溯噪音，
            # 失败详情即 junit/日志的全部输出
            raise pytest.fail.Exception(
                failure_detail(self.case, response, exc)) from None

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, pytest.fail.Exception):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def record_result(self, outcome: str, longrepr: str | None = None) -> None:
        """逐用例原始记录 → AITE_FT_RESULTS 指向的 jsonl（平台用例级落库数据源）。

        输入 = 渲染后的 messages + params，输出 = 模型响应，断言结果 = passed；
        AITE_FT_RESULTS 未设置（用户手跑 pytest）时不写——平台链路专属。
        """
        import json
        import os
        path = os.environ.get("AITE_FT_RESULTS")
        if not path:
            return
        entry = {
            "external_key": self.case.id,
            "desc": self.case.desc,
            "messages": self.case.rendered_messages(),
            "params": self.case.rendered_params(),
            "tools": self.case.tools,
            "expect": self.case.expect,
            "response": self._last_response,
            "passed": outcome == "passed",
            "error": (longrepr or "")[:2000] or None,
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def reportinfo(self):
        return self.path, 0, f"[{self.case.id}] {self.case.desc}"
