"""场景：工具调用 function calling（3 例）。

只断言确定性行为：正确选工具 / arguments 可解析且含关键参数。
不做"无关问题不调工具"（弱模型 flaky）。
"""

import json

WEATHER_AND_SEARCH = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "City name"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for arbitrary information",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

CALCULATOR_AND_WEATHER = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate an arithmetic expression",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        },
    },
    WEATHER_AND_SEARCH[0],
]


def _first_tool_call(chat_raw, prompt, tools) -> dict:
    data = chat_raw([{"role": "user", "content": prompt}], tools=tools)
    calls = data["choices"][0]["message"].get("tool_calls") or []
    assert calls, "模型未发起任何 tool call"
    return calls[0]["function"]


def test_selects_weather_tool(chat_raw):
    fn = _first_tool_call(chat_raw, "What is the weather in Tokyo right now?",
                          WEATHER_AND_SEARCH)
    assert fn["name"] == "get_weather"


def test_arguments_parseable_with_city(chat_raw):
    fn = _first_tool_call(chat_raw, "What is the weather in Tokyo right now?",
                          WEATHER_AND_SEARCH)
    args = json.loads(fn["arguments"])
    assert "tokyo" in json.dumps(args).lower()


def test_selects_calculator_tool(chat_raw):
    fn = _first_tool_call(chat_raw, "What is 12 times 7? Use the calculator.",
                          CALCULATOR_AND_WEATHER)
    assert fn["name"] == "calculator"
