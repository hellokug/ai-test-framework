"""Model pricing table (USD per 1M tokens) and cost estimation.

Values are rough public list prices, used only for budget guarding
(`--max-cost-usd` circuit breaker) and reporting. Local models (ollama,
vLLM self-hosted) default to zero cost unless priced explicitly.
"""

from __future__ import annotations

from aite.core.types import TokenUsage

# (prefix-match on model name) -> (input_usd_per_1m, output_usd_per_1m)
PRICING_TABLE: list[tuple[str, float, float]] = [
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4o", 2.50, 10.00),
    ("gpt-4-turbo", 10.00, 30.00),
    ("gpt-4.1-mini", 0.40, 1.60),
    ("gpt-4.1", 2.00, 8.00),
    ("gpt-4.5", 75.00, 150.00),
    ("o1-mini", 1.10, 4.40),
    ("o1", 15.00, 60.00),
    ("o3-mini", 1.10, 4.40),
    ("claude-opus-4", 15.00, 75.00),
    ("claude-sonnet-4", 3.00, 15.00),
    ("claude-3-7-sonnet", 3.00, 15.00),
    ("claude-3-5-sonnet", 3.00, 15.00),
    ("claude-3-5-haiku", 0.80, 4.00),
    ("claude-3-haiku", 0.25, 1.25),
    ("gemini-2.0-flash", 0.10, 0.40),
    ("gemini-1.5-pro", 1.25, 5.00),
    ("gemini-1.5-flash", 0.075, 0.30),
    ("deepseek-chat", 0.27, 1.10),
    ("deepseek-reasoner", 0.55, 2.19),
    ("qwen-max", 1.60, 6.40),
    ("qwen-plus", 0.40, 1.20),
    ("qwen-turbo", 0.05, 0.20),
    ("glm-4-plus", 7.00, 7.00),
    ("glm-4-air", 0.60, 0.60),
    ("doubao-pro", 0.11, 0.28),
    ("moonshot-v1", 1.80, 1.80),
    ("llama", 0.0, 0.0),
]

ZERO_COST_PREFIXES = ("ollama", "localhost", "127.0.0.1", "vllm", "local/")


def lookup_price(model: str | None) -> tuple[float, float] | None:
    """Return (input, output) USD per 1M tokens for the best-matching model, or None."""
    if not model:
        return None
    name = model.lower()
    if any(name.startswith(p) or f"/{p}" in name for p in ZERO_COST_PREFIXES):
        return (0.0, 0.0)
    best: tuple[str, tuple[float, float]] | None = None
    for prefix, in_price, out_price in PRICING_TABLE:
        if name.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, (in_price, out_price))
    return best[1] if best else None


def estimate_cost_usd(model: str | None, usage: TokenUsage | None) -> float | None:
    """Estimate USD cost of one request; None when usage unknown and not free."""
    if usage is None:
        return 0.0 if lookup_price(model) == (0.0, 0.0) else None
    price = lookup_price(model)
    if price is None:
        return None
    in_price, out_price = price
    return round(
        usage.prompt_tokens / 1_000_000 * in_price + usage.completion_tokens / 1_000_000 * out_price,
        8,
    )
