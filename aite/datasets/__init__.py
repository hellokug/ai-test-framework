"""Builtin dataset loaders."""

from aite.datasets import builtin, local  # noqa: F401


def try_register_hf() -> bool:
    try:
        from aite.datasets import hf_hub  # noqa: F401
        return True
    except ImportError:
        return False


# hf_hub 在线加载器：装了 aite[bench] 就随包注册（否则静默跳过）。
# 必须在包导入时注册，aite run / web 的解析链路才会认 type: hf_hub。
try_register_hf()


def builtin_ids() -> list[str]:
    """Ids available as builtin (packaged samples + user-imported from ./data)."""
    return builtin.available_ids()


def builtin_info(builtin_id: str) -> dict:
    return builtin.merged_info().get(builtin_id, {})
