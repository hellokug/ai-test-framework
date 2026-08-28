"""Builtin connectors. Importing this module registers all builtin components."""

from aite.connectors import anthropic, custom_http, gemini, openai_compatible  # noqa: F401

__all__ = ["openai_compatible", "anthropic", "gemini", "custom_http"]


def try_register_agent_connectors() -> list[str]:
    """Optionally register langchain connector when aite[agent] is installed."""
    registered: list[str] = []
    try:
        from aite.connectors import langchain_agent  # noqa: F401
        registered.append("langchain_agent")
    except ImportError:
        pass
    return registered
