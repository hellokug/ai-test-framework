"""pytest 适配器单测（build_config 纯函数 / junit 解析 / 离线 subprocess 端到端）。

全部离线：不发起任何模型请求；端到端用 fixtures 下的纯断言场景。
"""

from pathlib import Path

import pytest

from aite.tools.base import ToolRunRequest
from aite.tools.pytest_adapter import SCENARIOS_DIR, PytestAdapter

FT_FIXTURES = Path(__file__).parent.parent / "fixtures" / "ft_scenarios"


def make_request(**kw):
    base = dict(
        run_name="ft",
        target={"type": "openai_compatible", "base_url": "http://localhost:11434/v1",
                "model": "qwen2.5:3b"},
        datasets=["basic_chat"],
        work_dir=Path("./outputs"),
    )
    base.update(kw)
    return ToolRunRequest(**base)


class TestBuildConfig:
    def test_builtin_scenario_mapping(self):
        cfg = PytestAdapter().build_config(make_request(work_dir=Path("w")))
        (sc,) = cfg["scenarios"]
        assert sc["name"] == "basic_chat"
        assert sc["dir"] == str(SCENARIOS_DIR / "basic_chat")
        assert sc["junit_xml"] == str(Path("w/junit/basic_chat.xml"))
        assert sc["log"] == str(Path("w/logs/basic_chat.log"))
        argv = cfg["argv"]["basic_chat"]
        assert argv[1:3] == ["-m", "pytest"]
        assert str(SCENARIOS_DIR / "basic_chat") in argv
        assert f"--junitxml={sc['junit_xml']}" in argv
        assert "aite.functional.harness" in argv       # 插件按导入路径加载
        assert "norecursedirs=" in argv                # 显式目录不被 norecursedirs 拦
        assert "no:cacheprovider" in argv              # 不在 rootdir 落缓存
        assert cfg["timeout"] == 1800

    def test_env_injection(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        cfg = PytestAdapter().build_config(make_request())
        assert cfg["env"]["AITE_FT_BASE_URL"] == "http://localhost:11434/v1/chat/completions"
        assert cfg["env"]["AITE_FT_MODEL"] == "qwen2.5:3b"
        assert cfg["env"]["AITE_FT_API_KEY"] == "sk-x"

    def test_unknown_scenario_lists_available(self):
        with pytest.raises(ValueError) as ei:
            PytestAdapter().build_config(make_request(datasets=["nope"]))
        assert "nope" in str(ei.value)
        assert "basic_chat" in str(ei.value) and "long_context" in str(ei.value)

    def test_tests_dir_all_scenarios_when_datasets_empty(self, tmp_path):
        (tmp_path / "s1").mkdir()
        (tmp_path / "s1/test_a.py").write_text("def test_x():\n    pass\n")
        (tmp_path / "s2").mkdir()
        (tmp_path / "s2/test_b.py").write_text("def test_y():\n    pass\n")
        (tmp_path / "not_a_scenario").mkdir()          # 无 test_*.py，被忽略
        cfg = PytestAdapter().build_config(
            make_request(datasets=[], tests_dir=str(tmp_path)))
        assert [s["name"] for s in cfg["scenarios"]] == ["s1", "s2"]

    def test_tests_dir_datasets_as_filter(self, tmp_path):
        for name in ("alpha", "beta"):
            d = tmp_path / name
            d.mkdir()
            (d / "test_x.py").write_text("def test_x():\n    pass\n")
        cfg = PytestAdapter().build_config(
            make_request(datasets=["beta"], tests_dir=str(tmp_path)))
        assert [s["name"] for s in cfg["scenarios"]] == ["beta"]

    def test_missing_tests_dir_raises(self, tmp_path):
        with pytest.raises(ValueError, match="不存在"):
            PytestAdapter().build_config(
                make_request(datasets=[], tests_dir=str(tmp_path / "nope")))

    def test_timeout_from_extra(self):
        cfg = PytestAdapter().build_config(make_request(extra={"timeout": 60}))
        assert cfg["timeout"] == 60

    def test_missing_model_raises(self):
        with pytest.raises(ValueError, match="target.model"):
            PytestAdapter().build_config(
                make_request(target={"type": "openai_compatible",
                                     "base_url": "http://x/v1"}))

    def test_list_tasks_builtin_only(self):
        tasks = PytestAdapter().list_tasks()
        names = [t["name"] for t in tasks]
        assert names == ["basic_chat", "long_context", "structured_output", "tool_calling"]
        assert all(t["metrics"] == ["pass_rate"] and t["tags"] == ["functional"]
                   and t["few_shot_num"] is None for t in tasks)


JUNIT_MIXED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5" failures="1" errors="1" skipped="1" time="0.1">
<testcase classname="a" name="ok"/><testcase classname="a" name="skip"><skipped/></testcase>
<testcase classname="a" name="fail"><failure>boom</failure></testcase>
<testcase classname="a" name="err"><error>bam</error></testcase>
<testcase classname="b" name="ok2"/>
</testsuite></testsuites>"""


class TestCollect:
    def _write_junit(self, tmp_path, name, content):
        p = tmp_path / "junit" / f"{name}.xml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        (tmp_path / "logs" / f"{name}.log").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "logs" / f"{name}.log").write_text("log", encoding="utf-8")

    def test_pass_rate_with_failures_errors_skipped(self, tmp_path):
        self._write_junit(tmp_path, "basic_chat", JUNIT_MIXED)
        result = PytestAdapter().collect(make_request(work_dir=tmp_path))
        row = result.rows[0]
        assert row.dataset == "pytest:basic_chat" and row.metric == "pass_rate"
        assert row.value == pytest.approx(2 / 4)   # (5-1-1-1)/(5-1)
        assert row.count == 4                       # 与分母同口径
        assert row.passed is None                   # 未配阈值无判定
        assert result.raw_score == {"basic_chat": 2 / 4}
        assert "basic_chat.xml" in result.report_files
        assert "basic_chat.log" in result.report_files

    def test_threshold_judgement(self, tmp_path):
        self._write_junit(tmp_path, "basic_chat", JUNIT_MIXED)
        result = PytestAdapter().collect(
            make_request(work_dir=tmp_path, metric_thresholds={"pass_rate": 0.6}))
        assert result.rows[0].threshold == 0.6
        assert result.rows[0].passed is False

    def test_auto_threshold_is_zero_tolerance(self, tmp_path):
        self._write_junit(tmp_path, "basic_chat", JUNIT_MIXED)
        result = PytestAdapter().collect(make_request(work_dir=tmp_path, auto_thresholds=True))
        assert result.rows[0].threshold == 1.0
        assert result.rows[0].passed is False

    def test_missing_junit_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="junit"):
            PytestAdapter().collect(make_request(work_dir=tmp_path))

    def test_zero_tests_raises(self, tmp_path):
        self._write_junit(tmp_path, "basic_chat",
                          '<testsuites><testsuite name="pytest" tests="0" failures="0"'
                          ' errors="0" skipped="0"/></testsuites>')
        with pytest.raises(RuntimeError, match="未收集到用例"):
            PytestAdapter().collect(make_request(work_dir=tmp_path))


async def test_run_offline_end_to_end(tmp_path):
    """fixtures 场景真实 subprocess 跑通：全过 1.0 / 含失败 0.5，日志与 junit 落档。"""
    result = await PytestAdapter().run(make_request(
        datasets=[], tests_dir=str(FT_FIXTURES), work_dir=tmp_path,
        metric_thresholds={"pass_rate": 0.6}))
    by_ds = {r.dataset: r for r in result.rows}
    assert by_ds["pytest:pass_all"].value == 1.0
    assert by_ds["pytest:pass_all"].passed is True
    assert by_ds["pytest:with_failure"].value == pytest.approx(0.5)
    assert by_ds["pytest:with_failure"].passed is False
    assert result.raw_score == {"pass_all": 1.0, "with_failure": 0.5}
    assert (tmp_path / "junit" / "pass_all.xml").is_file()
    log = (tmp_path / "logs" / "with_failure.log").read_text(encoding="utf-8")
    assert "test_fail" in log                          # 失败详情留档
