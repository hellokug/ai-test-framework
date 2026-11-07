"""全局 common 配置：模型预制（profile 引用）与工具环境缺省。

解决「每个套件重抄 target 端点 / 工具环境参数」的预制问题——端点与环境在
``data/common.yaml`` 录入一次，suite 里一行引用::

    # data/common.yaml
    models:
      ollama-local:
        base_url: http://localhost:11434/v1
        model: qwen2.5:7b
        api_key_env: OPENAI_API_KEY
        rate_limit: {rpm: 120, concurrency: 8}
    tools:
      python_exe: ./.venv-evalscope/Scripts/python.exe

    # suite yaml
    target: {profile: ollama-local, params: {temperature: 0}}   # 内联逐键覆盖预制
    tools: {datasets: [aime26]}                                 # tools 缺省项自动并入

合并语义（易用 + 可定制）：profile 条目为底、suite 内联字段逐键覆盖，
dict 值（params/rate_limit/extra_args…）递归深合并；解析完成后 ``profile``
键移除，配置快照自包含（入库即可复现）。文件不存在 = 无预制（零影响）；
存在但损坏 → 显式报错（原则 9：静默丢预制会跑出「看似正常实则没用上
配置」的假结果）。apply_common 幂等，可在多段入口重复调用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from aite import config


def common_path() -> Path:
    """common 配置文件路径（项目 ./data/common.yaml，与数据集注册表同根）。"""
    return Path(config.DEFAULT_COMMON_FILE)


def load_common() -> dict[str, Any]:
    """读取 common 配置；文件不存在返回空 dict（无预制，零影响）。"""
    path = common_path()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        raise ValueError(f"common 配置解析失败 {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def upsert_model_profile(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """模型管理注册/更新 → 同步 common.yaml 模型预制（models 段合并，其余保留）。

    YAML 重写会丢失注释（未引入 ruamel）——预制以机器管理为主时使用；
    手工维护的预制请直接编辑文件。返回写入后的条目。
    """
    if not name or not isinstance(entry, dict):
        raise ValueError(f"非法模型预制: {name!r}")
    path = common_path()
    data = load_common()
    models = data.get("models") or {}
    clean = {k: v for k, v in entry.items() if v not in (None, {}, [])}
    models[name] = clean
    data["models"] = models
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return clean


def remove_model_profile(name: str) -> bool:
    """删除 common.yaml 模型预制（模型管理删除时同步）；不存在返回 False。"""
    data = load_common()
    models = data.get("models") or {}
    if name not in models:
        return False
    del models[name]
    data["models"] = models
    path = common_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return True


def apply_common(raw: dict[str, Any],
                 common: dict[str, Any] | None = None) -> dict[str, Any]:
    """就地展开 suite 原始 dict 的预制引用：target/judge 模型 profile + tools 环境缺省。

    common 缺省从 common_path() 读取（测试/调用方可显式注入）。幂等（解析后
    profile 键已移除，重复调用为 no-op）；自研域在 load_suite、工具域在
    parse_tool_suite 调用——两处都是「raw dict → 结构化配置」的唯一边界。
    """
    common = load_common() if common is None else common
    if not common:
        return raw
    models = common.get("models") or {}
    for target in _target_dicts(raw):
        _resolve_profile(target, models)
    tools_defaults = common.get("tools") or {}
    suite_tools = raw.get("tools")
    if tools_defaults and isinstance(suite_tools, dict):
        merged = _merge_under(tools_defaults, suite_tools)   # suite 内联为准，common 补缺
        suite_tools.clear()
        suite_tools.update(merged)
    return raw


def _target_dicts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """suite 里全部 TargetConfig 形态的原始 dict：target + 各域 judge 的模型引用。

    自研域 judges[].model/models、工具域 tools.judge（evalscope 判官）、
    rageval.judge（RAGAS 判官）同形状同机制——预制一行引用，内联逐键覆盖。"""
    out: list[dict[str, Any]] = []
    if isinstance(raw.get("target"), dict):
        out.append(raw["target"])
    for judge in raw.get("judges") or []:
        if not isinstance(judge, dict):
            continue
        if isinstance(judge.get("model"), dict):
            out.append(judge["model"])
        for model in judge.get("models") or []:
            if isinstance(model, dict):
                out.append(model)
    for section in ("tools", "rageval"):
        block = raw.get(section)
        if isinstance(block, dict) and isinstance(block.get("judge"), dict):
            out.append(block["judge"])
    return out


def _resolve_profile(target: dict[str, Any], models: dict[str, Any]) -> None:
    """单个 target 的 profile 展开：预制条目为底、内联字段逐键覆盖；随后移除引用键。"""
    name = target.pop("profile", None)
    if name is None:
        return
    entry = models.get(name)
    if not isinstance(entry, dict):
        raise ValueError(
            f"unknown model profile {name!r}; registered in "
            f"{common_path()}: {sorted(models)}（录入见 examples/common.example.yaml）")
    merged = _merge_under(entry, target)
    target.clear()
    target.update(merged)


def _merge_under(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """深合并：以 base 为底，overrides 同名键覆盖；两侧均为 dict 时递归逐键合并。"""
    out: dict[str, Any] = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_under(out[key], value)
        else:
            out[key] = value
    return out
