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
- ``vars``（可选）：占位符变量，messages 中 ``{NAME}`` 被替换为生成值；
  目前支持 ``{"haystack": {chars, key_at, key}}``（确定性长文填充，needle 埋点）
- ``expect``（必填，至少一个断言）：
  ``contains`` / ``ignore_case`` / ``nonempty`` /
  ``json{path,type,eq,in,len,nonempty,item_type,item_nonempty,fields}``（空规格
  ``{}`` = 仅校验剥围栏后可解析，即 JSON 输出格式正确性）/
  ``tool`` / ``tool_args_contains``，以及质量类断言：
  ``no_garbled``（乱码）/ ``no_leak``（内部标签泄露，可 ``{extra_patterns}`` 扩展）/
  ``no_repeat``（复读）

模型连接信息从环境变量读取（由 pytest 适配器注入，不要直接 pytest 场景目录）：
``AITE_FT_BASE_URL`` / ``AITE_FT_MODEL`` / ``AITE_FT_API_KEY``（本地服务可缺省）。
网络错误 / 5xx 统一重试 1 次；语义断言不重试；4xx 配置类错误直接抛出。
环境变量缺失在**请求时**才失败（pytest.fail 带指引），收集不需要任何环境。
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections import Counter

import httpx
import pytest

RETRYABLE_TIMEOUT = 120.0   # 单请求超时（本地小模型推理可能较慢）
_COMPLETIONS = "/chat/completions"
CASES_FILENAME = "cases.jsonl"

_TYPES = {"str": str, "int": int, "list": list, "dict": dict}
_EXPECT_KEYS = {"contains", "ignore_case", "nonempty", "json", "tool",
                "tool_args_contains", "no_garbled", "no_leak", "no_repeat"}
_JSON_SPEC_KEYS = {"path", "type", "eq", "in", "len", "nonempty",
                   "item_type", "item_nonempty", "fields"}
_VAR_BUILDERS = {"haystack"}

_REPLY_BRIEF = 160   # 失败信息里实际回复的截断长度


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
          params: dict) -> dict:
    url = _request_url()
    payload: dict = {
        "model": _required_env("AITE_FT_MODEL"),
        "messages": messages,
        **params,
    }
    if tools:
        payload["tools"] = tools
    headers = {"Authorization": f"Bearer {os.environ.get('AITE_FT_API_KEY') or 'EMPTY'}"}
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
        resp.raise_for_status()               # 4xx 配置类错误直接抛出
        return resp.json()
    pytest.fail(f"模型服务请求失败（已重试 1 次）：{last_error}")  # pragma: no cover


_CLIENT: httpx.Client | None = None


def _shared_client() -> httpx.Client:
    """进程级共享 client（fixture 与 cases.jsonl 收集的 Item 共用）。"""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(timeout=RETRYABLE_TIMEOUT)
    return _CLIENT


def strip_fences(text: str) -> str:
    """剥离 Markdown 代码围栏（\`\`\`json ... \`\`\` → 内容）。"""
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


def _build_var(spec: dict) -> str:
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"vars 项必须是单键对象（如 {{\"haystack\": {{...}}}}），实际: {spec!r}")
    (kind, kw), = spec.items()
    if kind not in _VAR_BUILDERS:
        raise ValueError(f"未知 vars 生成器 {kind!r}（可用: {sorted(_VAR_BUILDERS)}）")
    if not isinstance(kw, dict) or not {"chars", "key_at", "key"} <= set(kw):
        raise ValueError("haystack 需要 chars / key_at / key 参数")
    return build_haystack(kw["chars"], kw["key_at"], str(kw["key"]))


def _brief(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= _REPLY_BRIEF else text[:_REPLY_BRIEF] + "…"


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
        missing = [k for k in ("id", "desc", "messages", "expect") if not raw.get(k)]
        if missing:
            raise ValueError(f"{source}: 缺少必填字段 {missing}（id/desc/messages/expect）")
        expect = raw["expect"]
        if not isinstance(expect, dict):
            raise ValueError(f"{source}: expect 必须是对象")
        unknown = set(expect) - _EXPECT_KEYS
        if unknown:
            raise ValueError(f"{source}: 未知断言键 {sorted(unknown)}（可用: {sorted(_EXPECT_KEYS)}）")
        _validate_json_spec(expect.get("json") or {}, f"{source}: json")
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
        for spec in self.vars.values():
            _build_var(spec)   # 加载期校验生成器规格

    def rendered_messages(self) -> list:
        """应用 vars 占位符后的 messages（{NAME} → 生成值）。"""
        if not self.vars:
            return self.messages
        values = {name: _build_var(spec) for name, spec in self.vars.items()}

        def _replace(obj):
            if isinstance(obj, str):
                for name, val in values.items():
                    obj = obj.replace("{" + name + "}", val)
                return obj
            if isinstance(obj, list):
                return [_replace(x) for x in obj]
            if isinstance(obj, dict):
                return {k: _replace(v) for k, v in obj.items()}
            return obj

        return _replace(copy.deepcopy(self.messages))

    def check(self, response: dict) -> None:
        """按 expect 校验响应（失败抛 AssertionError，信息含期望与实际）。"""
        choices = response.get("choices") or []
        message: dict = {}
        if choices:
            message = (choices[0] or {}).get("message") or {}
        content = (message.get("content") or "").strip()
        ic = bool(self.expect.get("ignore_case"))
        if self.expect.get("nonempty"):
            assert content, "期望回复非空，实际为空"
        if self.expect.get("no_garbled"):
            bad = _garbled_evidence(content)
            assert bad is None, f"期望无乱码，检测到 {bad}（回复: {_brief(content)}）"
        if "no_leak" in self.expect:
            hit = _leak_evidence(content, self._extra_leak)
            assert hit is None, f"期望无内部标签泄露，检测到 {hit!r}（回复: {_brief(content)}）"
        if self.expect.get("no_repeat"):
            ev = _repeat_evidence(content)
            assert ev is None, f"期望无复读，检测到 {ev}（回复: {_brief(content)}）"
        if "contains" in self.expect:
            needle = str(self.expect["contains"])
            hit = (needle.lower() in content.lower()) if ic else (needle in content)
            assert hit, f"期望回复包含 {needle!r}，实际: {_brief(content)}"
        if "json" in self.expect:
            try:
                data = json.loads(strip_fences(content))
            except ValueError as exc:
                raise AssertionError(
                    f"期望合法 JSON，解析失败: {exc}；实际: {_brief(content)}") from None
            _check_json_spec(self.expect["json"], data, "json", ic)
        tool_calls = message.get("tool_calls") or []
        first_tool = (tool_calls[0].get("function") or {}) if tool_calls else None
        if "tool" in self.expect:
            assert first_tool is not None, "期望发起 tool call，实际没有"
            assert first_tool.get("name") == self.expect["tool"], (
                f"期望选择工具 {self.expect['tool']!r}，实际 {first_tool.get('name')!r}")
        if "tool_args_contains" in self.expect:
            assert first_tool is not None, "期望发起 tool call，实际没有"
            try:
                args = json.loads(first_tool.get("arguments") or "")
            except ValueError as exc:
                raise AssertionError(f"tool call arguments 不是合法 JSON: {exc}") from None
            dump = json.dumps(args, ensure_ascii=False).lower()
            needle = str(self.expect["tool_args_contains"]).lower()
            assert needle in dump, (
                f"期望 tool arguments 包含 {needle!r}，实际: {_brief(dump)}")


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


class CasesFile(pytest.File):
    def collect(self):
        for case in load_cases(self.path):
            yield CaseItem.from_parent(self, name=case.id, case=case)


class CaseItem(pytest.Item):
    def __init__(self, *, case: Case, **kwargs):
        super().__init__(**kwargs)
        self.case = case

    def runtest(self):
        response = _post(_shared_client(), self.case.rendered_messages(),
                         tools=self.case.tools, params=self.case.params)
        self.case.check(response)

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, AssertionError):
            return f"[{self.case.id}] {self.case.desc}\n  {excinfo.value}"
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"[{self.case.id}] {self.case.desc}"
