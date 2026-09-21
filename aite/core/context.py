"""EvalContext construction (engine-side wiring of gateway services)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aite.core.types import EvalContext

if TYPE_CHECKING:
    from aite.core.loader import SuiteConfig
    from aite.gateway.llm_gateway import LLMGateway


def build_gateway(config: SuiteConfig) -> LLMGateway | None:
    """Create the shared LLM gateway when the suite needs LLM calls.

    需求判定走 Metric 契约的 ``needs_llm`` 类属性（注册表驱动，单一来源；
    不得回退到硬编码指标名单——新增 LLM 指标会漏）。模型注册：judge 模型优先；
    未配 judge 时把 target 自身注册为缺省判定模型（自评口径，指标
    ``params.model`` 可覆盖）。
    """
    from aite.core.registry import resolve_metric

    needs_llm = bool(config.judges)
    for m in config.metrics:
        try:
            if getattr(resolve_metric(m.name), "needs_llm", False):
                needs_llm = True
                break
        except KeyError:  # unknown metric: engine fail-fasts later with its own error
            continue
    if not needs_llm:
        return None
    from aite.gateway.llm_gateway import LLMGateway  # local import keeps core dependency-light

    gateway = LLMGateway(max_cost_usd=config.max_cost_usd)
    judge_models: list[Any] = [j.model for j in config.judges if j.model is not None]
    judge_models += [m for j in config.judges for m in j.models]
    for model_cfg in judge_models:
        gateway.register_model(model_cfg)
    if not judge_models:
        gateway.register_model(config.target, make_default=True)
    return gateway


def build_context(config: SuiteConfig, gateway: LLMGateway | None, run_id: str | None) -> EvalContext:
    return EvalContext(
        run_name=config.run_name,
        run_id=run_id,
        gateway=gateway,
        config=config.model_dump(mode="json", exclude={"storage"}),
    )
