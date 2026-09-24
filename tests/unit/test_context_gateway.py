"""build_gateway 单测：needs_llm 注册表驱动 + 无 judge 时 target 自评缺省模型。"""

import aite.metrics  # noqa: F401  (生产由 engine 导入触发指标注册，测试显式触发)
from aite.core.context import build_gateway
from aite.core.loader import load_suite


def _suite(metrics: list, judges: list | None = None):
    data = {
        "run_name": "gw-test",
        "target": {"type": "openai_compatible", "base_url": "http://x",
                   "model": "target-model"},
        "datasets": [{"type": "builtin", "id": "hotpotqa"}],
        "metrics": metrics,
    }
    if judges:
        data["judges"] = judges
    return load_suite(data)


def test_rule_only_suite_no_gateway():
    assert build_gateway(_suite([{"exact_match": {}}])) is None


def test_needs_llm_from_metric_contract():
    """回归：LLM 能力判定走 Metric.needs_llm，不依赖硬编码名单——
    新增/改名 LLM 指标不再漏建 gateway。"""
    gateway = build_gateway(_suite([{"task_completion": {}}]))
    assert gateway is not None
    assert gateway.default_model is not None


def test_target_registered_as_default_when_no_judge_model():
    gateway = build_gateway(_suite([{"task_completion": {}}]))
    assert gateway is not None
    # complete(model=None) 走缺省模型：未配 judge 时 = target 自身（自评口径）
    assert gateway.default_model == "target-model"


def test_judge_model_registered_as_default():
    suite = _suite([{"task_completion": {}}], judges=[
        {"type": "score", "model": {"type": "openai_compatible",
                                    "base_url": "http://j", "model": "judge-x"}}])
    gateway = build_gateway(suite)
    assert gateway is not None
    assert gateway.default_model == "judge-x"
