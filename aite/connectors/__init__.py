"""Builtin connectors. Importing this module registers all builtin components."""

from aite.connectors import (  # noqa: F401
    anthropic,
    custom_http,
    gemini,
    openai_compatible,
    predictions_file,
)

__all__ = ["openai_compatible", "anthropic", "gemini", "custom_http", "predictions_file"]


def try_register_agent_connectors() -> list[str]:
    """Optionally register langchain connector when aite[agent] is installed."""
    registered: list[str] = []
    try:
        from aite.connectors import langchain_agent  # noqa: F401
        registered.append("langchain_agent")
    except ImportError:
        pass
    return registered
