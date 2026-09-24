"""Registry tests: registration, resolution, plugin import."""

import pytest

import aite.connectors  # noqa: F401
import aite.datasets  # noqa: F401
import aite.metrics  # noqa: F401
from aite.core import registry


def test_builtin_components_registered():
    assert "openai_compatible" in registry.registry.connectors
    assert "local" in registry.registry.datasets
    assert "builtin" in registry.registry.datasets
    for metric in ("exact_match", "rouge_l", "latency_p95_ms", "repeat_consistency",
                   "tool_call_correctness", "attack_success_rate"):
        assert metric in registry.registry.metrics, metric
    assert "score" in registry.registry.judges


def test_resolve_unknown_raises():
    with pytest.raises(KeyError, match="unknown connector"):
        registry.resolve_connector("nope")
    with pytest.raises(KeyError, match="unknown metric"):
        registry.resolve_metric("nope")


def test_register_decorator_sets_name():
    @registry.register_metric("custom_test_metric")
    class Custom:
        pass

    assert registry.registry.metrics["custom_test_metric"] is Custom
    del registry.registry.metrics["custom_test_metric"]


def test_load_plugins_imports_module():
    registry.load_plugins(["tests.fixtures.plugin_metrics"])  # noqa: F401
    assert "fixture_hello_metric" in registry.registry.metrics
    # 裸类不满足 Metric ABC，必须出注册表——否则全量跑时污染
    # test_registry_contract（契约测试单独跑抓不到此顺序依赖）
    del registry.registry.metrics["fixture_hello_metric"]


def test_load_plugins_survives_bad_module():
    registry.load_plugins(["not.a.real.module"])
    # still functional afterwards
    assert registry.resolve_metric("exact_match")
