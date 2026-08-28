"""Component registry + plugin loading.

Three ways to extend aite:
1. builtin components decorated at import time (``@register_connector`` ...)
2. YAML ``plugins: [mypkg.metrics]`` - module imported before resolution
3. installed distributions exposing entry points in group ``aite.plugins``
"""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, TypeVar

from aite.core.interfaces import Dataset, Judge, Metric, ReportRenderer, TargetSystem

if TYPE_CHECKING:
    pass

logger = logging.getLogger("aite.registry")

C = TypeVar("C")


class _Registry:
    def __init__(self) -> None:
        self.connectors: dict[str, type[TargetSystem]] = {}
        self.datasets: dict[str, type[Dataset]] = {}
        self.metrics: dict[str, type[Metric]] = {}
        self.judges: dict[str, type[Judge]] = {}
        self.renderers: dict[str, type[ReportRenderer]] = {}

    def clear(self) -> None:
        """Test helper: drop all registrations (builtins re-registered on import)."""
        for d in (self.connectors, self.datasets, self.metrics, self.judges, self.renderers):
            d.clear()


registry = _Registry()


def register_connector(name: str):
    def deco(cls):
        cls.name = name
        registry.connectors[name] = cls
        return cls
    return deco


def register_dataset(name: str):
    def deco(cls):
        cls.name = name
        registry.datasets[name] = cls
        return cls
    return deco


def register_metric(name: str):
    def deco(cls):
        cls.name = name
        registry.metrics[name] = cls
        return cls
    return deco


def register_judge(name: str):
    def deco(cls):
        cls.name = name
        registry.judges[name] = cls
        return cls
    return deco


def register_renderer(name: str):
    def deco(cls):
        cls.name = name
        registry.renderers[name] = cls
        return cls
    return deco


def load_plugins(modules: list[str] | None = None) -> None:
    """Import explicitly-listed plugin modules, then scan entry points (once)."""
    for mod in modules or []:
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001 - plugin failures must not kill the host
            logger.warning("failed to load aite plugin module '%s'", mod, exc_info=True)
    for ep in entry_points(group="aite.plugins"):
        try:
            importlib.import_module(ep.value)
            logger.debug("loaded aite plugin entry point %s -> %s", ep.name, ep.value)
        except Exception:  # noqa: BLE001
            logger.warning("failed to load aite plugin %s (%s)", ep.name, ep.value, exc_info=True)


def resolve_connector(name: str) -> type[TargetSystem]:
    try:
        return registry.connectors[name]
    except KeyError:
        raise KeyError(
            f"unknown connector '{name}'. available: {sorted(registry.connectors)}"
        ) from None


def resolve_dataset(name: str) -> type[Dataset]:
    try:
        return registry.datasets[name]
    except KeyError:
        raise KeyError(
            f"unknown dataset '{name}'. available: {sorted(registry.datasets)}"
        ) from None


def resolve_metric(name: str) -> type[Metric]:
    try:
        return registry.metrics[name]
    except KeyError:
        raise KeyError(
            f"unknown metric '{name}'. available: {sorted(registry.metrics)}"
        ) from None


def resolve_judge(name: str) -> type[Judge]:
    try:
        return registry.judges[name]
    except KeyError:
        raise KeyError(
            f"unknown judge '{name}'. available: {sorted(registry.judges)}"
        ) from None


def resolve_renderer(name: str) -> type[ReportRenderer]:
    try:
        return registry.renderers[name]
    except KeyError:
        raise KeyError(
            f"unknown report format '{name}'. available: {sorted(registry.renderers)}"
        ) from None
