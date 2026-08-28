"""EvalContext construction (engine-side wiring of gateway services)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aite.core.types import EvalContext

if TYPE_CHECKING:
    from aite.core.loader import SuiteConfig
    from aite.gateway.llm_gateway import LLMGateway


def build_gateway(config: SuiteConfig) -> LLMGateway | None:
    """Create the shared LLM gateway when the suite needs LLM calls
    (judges configured, LLM-based metrics, or embedding metrics)."""
    needs_llm = bool(config.judges)
    metric_names = {m.name for m in config.metrics}
    if metric_names & {
        "faithfulness", "answer_relevancy", "trajectory_quality", "task_completion",
        "prompt_injection_resistance", "attack_success_rate", "llm_score",
    } | {m.name for m in config.metrics if m.name.startswith("judge_")}:
        needs_llm = True
    if not needs_llm:
        return None
    from aite.gateway.llm_gateway import LLMGateway  # local import keeps core dependency-light

    gateway = LLMGateway(max_cost_usd=config.max_cost_usd)
    judge_models: list[Any] = [j.model for j in config.judges if j.model is not None]
    judge_models += [m for j in config.judges for m in j.models]
    for model_cfg in judge_models:
        if model_cfg is not None:
            gateway.register_model(model_cfg)
    return gateway


def build_context(config: SuiteConfig, gateway: LLMGateway | None, run_id: str | None) -> EvalContext:
    return EvalContext(
        run_name=config.run_name,
        run_id=run_id,
        gateway=gateway,
        config=config.model_dump(mode="json", exclude={"storage"}),
    )
