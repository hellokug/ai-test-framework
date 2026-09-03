"""功能测试 harness（pytest 插件，经 ``-p aite.functional.harness`` 加载）。

模型连接信息从环境变量读取（由 pytest 适配器注入，不要直接 pytest 场景目录）：

- ``AITE_FT_BASE_URL``：OpenAI 兼容 base_url（如 http://localhost:11434/v1）
- ``AITE_FT_MODEL``：模型名
- ``AITE_FT_API_KEY``：API key（本地服务可缺省，按 EMPTY 处理）

提供的能力：

- session 级 ``httpx.Client``（单请求超时 120s）
- ``chat`` fixture：messages → 助手文本回复；网络错误 / 5xx 统一重试 1 次
  （语义断言不重试，4xx 配置类错误直接抛出）
- ``chat_raw`` fixture：返回完整响应 dict（tool_calls 断言等需要原始结构）
- ``strip_fences()``：剥离 Markdown 代码围栏（结构化输出场景用）

环境变量缺失在**请求时**才失败（pytest.fail 带指引），保证 ``--collect-only``
无需任何环境即可收集。
"""

from __future__ import annotations

import os

import httpx
import pytest

RETRYABLE_TIMEOUT = 120.0   # 单请求超时（本地小模型推理可能较慢）
_COMPLETIONS = "/chat/completions"


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
    with httpx.Client(timeout=RETRYABLE_TIMEOUT) as client:
        yield client


@pytest.fixture()
def chat(_client: httpx.Client):
    """messages → 助手文本回复（str）。"""
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
