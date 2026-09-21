"""注册表契约：所有注册组件满足接口签名与命名一致（原则 10「契约同步演进」的机器化）。

cls.name 与注册名漂移、Metric 类属性契约（aggregation 合法值 / 方向布尔）、
组件脱离 ABC、chaos 武器目录（kinds × param_schemas）不自洽——此前只靠 review，
现在注册表一变即红，engine 无需在运行时静默改写 res.metric 兜底（见 engine._eval_entry）。
"""

import aite.core.engine  # noqa: F401  (导入即完成 builtin 组件注册)
from aite.chaos.injectors.base import (
    Injector,
    get_injector,
    list_injectors,
    list_weapons,
)
from aite.core.interfaces import Dataset, Judge, Metric, ReportRenderer, TargetSystem
from aite.core.registry import registry

_KINDS = ("connectors", "datasets", "metrics", "judges", "renderers")
_AGGREGATIONS = {"mean", "rate", "p50", "p90", "p95", "p99", "min", "max", "sum"}
_SCHEMA_TYPES = {"str", "int", "float", "bool"}


def test_every_registered_class_carries_its_name():
    for kind in _KINDS:
        for name, cls in getattr(registry, kind).items():
            assert getattr(cls, "name", None) == name, (
                f"{kind}:{name} 的 cls.name 漂移为 {getattr(cls, 'name', None)!r}"
            )


def test_metrics_satisfy_metric_contract():
    assert registry.metrics, "metrics 注册表为空（builtin 注册未触发？）"
    for name, cls in registry.metrics.items():
        assert issubclass(cls, Metric), f"metric {name} 未实现 Metric ABC"
        assert cls.aggregation in _AGGREGATIONS, f"{name}.aggregation 非法: {cls.aggregation}"
        assert isinstance(cls.higher_is_better, bool), f"{name}.higher_is_better 须为布尔"
        assert isinstance(cls.needs_llm, bool), f"{name}.needs_llm 须为布尔"


def test_judges_connectors_datasets_renderers_implement_abstract_bases():
    for kind, base in (("judges", Judge), ("connectors", TargetSystem),
                       ("datasets", Dataset), ("renderers", ReportRenderer)):
        for name, cls in getattr(registry, kind).items():
            assert issubclass(cls, base), f"{kind}:{name} 未实现 {base.__name__}"


def test_injectors_implement_lifecycle_contract():
    for name in list_injectors():
        assert isinstance(get_injector(name), Injector), f"注入器 {name} 未实现 Injector ABC"


def test_chaos_weapon_catalog_is_self_consistent():
    """武器目录是前端表单/用例编写的数据源：kinds 与 param_schemas 不自洽即坏表单。"""
    weapons = list_weapons()
    assert weapons, "武器目录为空"
    assert {w["injector"] for w in weapons} == set(list_injectors())
    for weapon in weapons:
        assert set(weapon["schemas"]) <= set(weapon["kinds"]), (
            f"{weapon['injector']}: param_schemas 出现 kinds 之外的故障类型"
        )
        for kind, schema in weapon["schemas"].items():
            for pname, spec in schema.items():
                assert spec.get("type", "str") in _SCHEMA_TYPES, (
                    f"{weapon['injector']}.{kind}.{pname} 未知 type: {spec.get('type')}"
                )
                if spec.get("required"):
                    assert "default" not in spec, (
                        f"{weapon['injector']}.{kind}.{pname} 必填却带默认值"
                    )
