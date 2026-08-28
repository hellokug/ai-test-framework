"""Fixture plugin module for registry tests."""

from aite.core.registry import register_metric


@register_metric("fixture_hello_metric")
class HelloMetric:
    pass
