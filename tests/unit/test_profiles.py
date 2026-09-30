"""common 预制（profiles）合并语义测试：模型 profile 引用 + 工具环境缺省。

覆盖：展开/逐键覆盖/深合并、未知 profile 显式报错、幂等（parse_tool_suite
一次运行多次进入）、judges 模型引用、无 common 文件零影响、两条入口边界
（load_suite 自研域 / parse_tool_suite 工具域）真实生效。
"""

import copy

import pytest

from aite.core import profiles
from aite.core.loader import load_suite
from aite.tools import parse_tool_suite

COMMON = {
    "models": {
        "ollama-local": {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:7b",
            "api_key_env": "OPENAI_API_KEY",
            "params": {"temperature": 0.2, "max_tokens": 4096},
            "rate_limit": {"rpm": 120, "concurrency": 8},
        },
    },
    "tools": {
        "extra_args": {"python_exe": "./.venv-evalscope/Scripts/python.exe",
                       "timeout_s": 14400},
    },
}


def test_profile_expand_inline_wins_per_key():
    raw = {"target": {"profile": "ollama-local", "model": "qwen2.5:14b",
                      "params": {"temperature": 0}}}
    tgt = profiles.apply_common(raw, COMMON)["target"]
    assert "profile" not in tgt                      # 引用键移除，快照自包含
    assert tgt["base_url"] == "http://localhost:11434/v1"   # 预制补齐
    assert tgt["model"] == "qwen2.5:14b"             # 内联覆盖预制
    assert tgt["api_key_env"] == "OPENAI_API_KEY"    # 预制补齐
    assert tgt["params"] == {"temperature": 0, "max_tokens": 4096}  # params 逐键合并
    assert tgt["rate_limit"] == {"rpm": 120, "concurrency": 8}      # 预制整段带入


def test_unknown_profile_explicit_error():
    with pytest.raises(ValueError, match=r"unknown model profile 'nope'"):
        profiles.apply_common({"target": {"profile": "nope"}}, COMMON)


def test_idempotent_double_apply():
    raw = {"target": {"profile": "ollama-local", "params": {"temperature": 0}}}
    once = profiles.apply_common(raw, COMMON)
    twice = profiles.apply_common(once, COMMON)      # parse_tool_suite 一次运行多次进入
    assert twice == copy.deepcopy(once)
    assert "profile" not in twice["target"]


def test_judge_models_resolved():
    raw = {"target": {"base_url": "http://t/v1", "model": "m"},
           "judges": [
               {"type": "score", "model": {"profile": "ollama-local"}},
               {"type": "multi", "models": [{"profile": "ollama-local"},
                                            {"base_url": "http://b/v2", "model": "x"}]},
           ]}
    out = profiles.apply_common(raw, COMMON)
    assert out["judges"][0]["model"]["base_url"] == "http://localhost:11434/v1"
    assert out["judges"][1]["models"][0]["model"] == "qwen2.5:7b"
    assert out["judges"][1]["models"][1] == {"base_url": "http://b/v2", "model": "x"}


def test_tool_domain_judge_profiles_resolved():
    """工具域判官预制引用：evalscope tools.judge 与 rageval 的 rageval.judge 同机制。"""
    common = copy.deepcopy(COMMON)
    common["models"]["deepseek"] = {"base_url": "https://api.deepseek.com/v1",
                                    "model": "deepseek-chat",
                                    "api_key_env": "DEEPSEEK_API_KEY"}
    raw = {"engine": "evalscope", "target": {"profile": "ollama-local"},
           "tools": {"datasets": ["aime26"], "judge": {"profile": "deepseek"}}}
    judge = profiles.apply_common(raw, common)["tools"]["judge"]
    assert "profile" not in judge                       # 引用键移除，幂等
    assert judge["base_url"] == "https://api.deepseek.com/v1"
    assert judge["model"] == "deepseek-chat"
    assert judge["api_key_env"] == "DEEPSEEK_API_KEY"

    raw = {"engine": "evalscope-rageval", "target": {"profile": "ollama-local"},
           "tools": {"datasets": ["./data/x.jsonl"]},
           "rageval": {"judge": {"profile": "deepseek", "temperature": 0.1}}}
    judge = profiles.apply_common(raw, common)["rageval"]["judge"]
    assert judge["model"] == "deepseek-chat"            # 预制补齐
    assert judge["temperature"] == 0.1                  # 内联逐键覆盖


def test_tools_defaults_merged_suite_wins():
    raw = {"engine": "evalscope",
           "tools": {"datasets": ["aime26"], "extra_args": {"timeout_s": 60}}}
    out = profiles.apply_common(raw, COMMON)
    assert out["tools"]["datasets"] == ["aime26"]     # suite 键不动
    assert out["tools"]["extra_args"] == {            # extra_args 深合并，内联覆盖
        "python_exe": "./.venv-evalscope/Scripts/python.exe", "timeout_s": 60}

    raw2 = {"engine": "evalscope", "tools": {"datasets": ["x"],
                                             "extra_args": {"python_exe": "p"}}}
    out2 = profiles.apply_common(raw2, COMMON)
    assert out2["tools"]["extra_args"] == {"python_exe": "p", "timeout_s": 14400}


def test_no_tools_section_untouched():
    raw = {"run_name": "r", "target": {"base_url": "http://t/v1", "model": "m"}}
    out = profiles.apply_common(raw, COMMON)
    assert "tools" not in out                         # 自研套件不被注入 tools 段


def _write_common(monkeypatch, tmp_path, text):
    common_file = tmp_path / "common.yaml"
    common_file.write_text(text, encoding="utf-8")
    monkeypatch.setattr(profiles, "common_path", lambda: common_file)


def test_load_suite_applies_profile(monkeypatch, tmp_path):
    _write_common(monkeypatch, tmp_path,
                  "models:\n  p1:\n    base_url: http://pf:1/v1\n    model: pm\n")
    cfg = load_suite({"run_name": "r", "module": "accuracy",
                      "target": {"profile": "p1"},
                      "datasets": [{"type": "builtin", "id": "gsm8k"}],
                      "metrics": [{"contains": {}}]})
    assert cfg.target.base_url == "http://pf:1/v1"    # 预制解析进强类型
    assert cfg.target.model == "pm"
    assert "profile" not in cfg.target.model_dump()


def test_parse_tool_suite_applies(monkeypatch, tmp_path):
    _write_common(monkeypatch, tmp_path,
                  "models:\n  p1:\n    base_url: http://pf:1/v1\n    model: pm\n"
                  "tools:\n  extra_args:\n    python_exe: py-env\n")
    raw = {"engine": "evalscope", "run_name": "t",
           "target": {"profile": "p1"},
           "tools": {"datasets": ["aime26"]}}
    req, snapshot = parse_tool_suite(raw)
    assert req.target["base_url"] == "http://pf:1/v1"
    assert req.extra["python_exe"] == "py-env"        # 环境缺省进了适配器请求
    assert snapshot["target"]["base_url"] == "http://pf:1/v1"  # 快照自包含


def test_missing_common_file_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, "common_path",
                        lambda: tmp_path / "absent.yaml")
    raw = {"target": {"profile": "p"}}
    out = profiles.apply_common(raw)
    assert out == {"target": {"profile": "p"}}        # 空 common 直接透传不误删


def test_broken_common_file_explicit_error(monkeypatch, tmp_path):
    _write_common(monkeypatch, tmp_path, "models: [broken")
    with pytest.raises(ValueError, match="common 配置解析失败"):
        profiles.load_common()
