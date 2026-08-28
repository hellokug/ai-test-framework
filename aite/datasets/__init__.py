"""Builtin dataset loaders."""

from aite.datasets import builtin, local  # noqa: F401


def try_register_hf() -> bool:
    try:
        from aite.datasets import hf_hub  # noqa: F401
        return True
    except ImportError:
        return False


def builtin_ids() -> list[str]:
    """Ids available as builtin (always importable, sample versions)."""
    return builtin.BUILTIN_IDS


def builtin_info(builtin_id: str) -> dict:
    return builtin.BUILTIN_INFO.get(builtin_id, {})
