"""pytest 适配器单测（build_config 纯函数 / junit 解析 / 离线 subprocess 端到端）。

全部离线：端到端用 fixtures 下的纯断言场景 + fake 模型服务（cases.jsonl 链路）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aite import config
from aite.tools.base import ToolRunRequest
from aite.tools.pytest_adapter import PytestAdapter

FT_FIXTURES = Path(__file__).parent.parent / "fixtures" / "ft_scenarios"
FT_JSONL = Path(__file__).parent.parent / "fixtures" / "ft_jsonl"


def make_request(**kw):
    base = dict(
        run_name="ft",
        target={"type": "openai_compatible", "base_url": "http://localhost:11434/v1",
                "model": "qwen2.5:3b"},
        tests_dir=str(FT_FIXTURES),
        datasets=[],
        work_dir=Path("./outputs"),
    )
    base.update(kw)
    return ToolRunRequest(**base)


def _jsonl_root(tmp_path, scenarios: dict[str, list[str]]) -> str:
    """临时场景根：{场景名: [用例 id]}，每场景一个 cases.jsonl。"""
    root = tmp_path / "ft"
    for name, ids in scenarios.items():
        d = root / name
        d.mkdir(parents=True)
        rows = "".join(
            json.dumps({"id": i, "desc": "d",
                        "messages": [{"role": "user", "content": "hi"}],
                        "expect": {"nonempty": True}}, ensure_ascii=False) + "\n"
            for i in ids)
        (d / "cases.jsonl").write_text(rows, encoding="utf-8")
    return str(root)


class TestBuildConfig:
    def test_default_root_is_managed_scenarios_dir(self, tmp_path, monkeypatch):
        """tests_dir 缺省 = 平台托管场景根（config.DEFAULT_FUNCTIONAL_SCENARIOS_DIR）。"""
        root = Path(_jsonl_root(tmp_path, {"s1": ["a"]}))
        monkeypatch.setattr(config, "DEFAULT_FUNCTIONAL_SCENARIOS_DIR", str(root))
        cfg = PytestAdapter().build_config(make_request(
            datasets=["s1"], tests_dir=None, work_dir=Path("w")))
        (sc,) = cfg["scenarios"]
        assert sc["name"] == "s1"
        assert sc["targets"] == [str(root / "s1")]
        assert sc["junit_xml"] == str(Path("w/junit/s1.xml"))
        assert sc["log"] == str(Path("w/logs/s1.log"))
        argv = cfg["argv"]["s1"]
        assert argv[1:3] == ["-m", "pytest"]
        assert str(root / "s1") in argv
        assert f"--junitxml={sc['junit_xml']}" in argv
        assert "aite.functional.harness" in argv       # 插件按导入路径加载
        assert "norecursedirs=" in argv                # 显式目录不被 norecursedirs 拦
        assert "no:cacheprovider" in argv              # 不在 rootdir 落缓存
        assert cfg["timeout"] == 1800
        assert cfg["skipped"] == []                    # 无 cases 过滤不跳过任何场景

    def test_cases_filter_targets_exact_nodeids(self, tmp_path):
        """cases 过滤 → cases.jsonl::<id> 精确 nodeid（非 -k 子串匹配）。"""
        root = _jsonl_root(tmp_path, {"smoke": ["keyword", "intro"]})
        cfg = PytestAdapter().build_config(make_request(
            tests_dir=root, cases=["keyword", "intro"], work_dir=Path("w")))
        (sc,) = cfg["scenarios"]
        assert sc["targets"] == [str(Path(root) / "smoke" / "cases.jsonl") + "::keyword",
                                 str(Path(root) / "smoke" / "cases.jsonl") + "::intro"]

    def test_cases_filter_unknown_id_lists_available(self, tmp_path):
        root = _jsonl_root(tmp_path, {"smoke": ["intro", "yes_no"]})
        with pytest.raises(ValueError) as ei:
            PytestAdapter().build_config(
                make_request(tests_dir=root, cases=["nope"]))
        assert "nope" in str(ei.value)
        assert "intro" in str(ei.value) and "yes_no" in str(ei.value)  # 列出可用 id

    def test_cases_filter_matches_repeat_runs(self, tmp_path):
        """repeat 用例：原 id 选中其全部轮次，id[rN] 精确复现单轮。"""
        root = tmp_path / "ft"
        d = root / "smoke"
        d.mkdir(parents=True)
        base = {"desc": "d", "messages": [{"role": "user", "content": "hi"}],
                "expect": {"nonempty": True}}
        rows = "".join(json.dumps({**base, "id": i, **extra}, ensure_ascii=False) + "\n"
                       for i, extra in [("intro", {"repeat": 3}), ("other", {})])
        (d / "cases.jsonl").write_text(rows, encoding="utf-8")
        cfg = PytestAdapter().build_config(make_request(tests_dir=str(root), cases=["intro"]))
        targets = cfg["scenarios"][0]["targets"]
        assert [t.split("::", 1)[1] for t in targets] == ["intro", "intro[r2]", "intro[r3]"]
        cfg = PytestAdapter().build_config(
            make_request(tests_dir=str(root), cases=["intro[r3]", "other"]))
        targets = cfg["scenarios"][0]["targets"]
        assert [t.split("::", 1)[1] for t in targets] == ["intro[r3]", "other"]

    def test_cases_filter_skips_scenarios_without_match(self, tmp_path):
        root = _jsonl_root(tmp_path, {"s1": ["intro"], "s2": ["other"]})
        cfg = PytestAdapter().build_config(make_request(
            tests_dir=root, datasets=["s1", "s2"], cases=["intro"]))
        assert [s["name"] for s in cfg["scenarios"]] == ["s1"]
        assert cfg["skipped"] == ["s2"]

    def test_cases_filter_requires_jsonl_scenario(self, tmp_path):
        (tmp_path / "handwritten").mkdir()
        (tmp_path / "handwritten" / "test_a.py").write_text("def test_x():\n    pass\n")
        with pytest.raises(ValueError, match="数据驱动"):
            PytestAdapter().build_config(
                make_request(datasets=[], tests_dir=str(tmp_path), cases=["x"]))

    def test_env_injection(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        cfg = PytestAdapter().build_config(make_request())
        assert cfg["env"]["AITE_FT_BASE_URL"] == "http://localhost:11434/v1/chat/completions"
        assert cfg["env"]["AITE_FT_MODEL"] == "qwen2.5:3b"
        assert cfg["env"]["AITE_FT_API_KEY"] == "sk-x"

    def test_unknown_scenario_lists_available(self, tmp_path):
        root = _jsonl_root(tmp_path, {"s1": ["a"], "s2": ["b"]})
        with pytest.raises(ValueError) as ei:
            PytestAdapter().build_config(
                make_request(tests_dir=root, datasets=["nope"]))
        assert "nope" in str(ei.value)
        assert "s1" in str(ei.value) and "s2" in str(ei.value)

    def test_tests_dir_all_scenarios_when_datasets_empty(self, tmp_path):
        (tmp_path / "s1").mkdir()
        (tmp_path / "s1/test_a.py").write_text("def test_x():\n    pass\n")
        (tmp_path / "s2").mkdir()
        (tmp_path / "s2/test_b.py").write_text("def test_y():\n    pass\n")
        (tmp_path / "not_a_scenario").mkdir()          # 无 test_*.py，被忽略
        cfg = PytestAdapter().build_config(
            make_request(datasets=[], tests_dir=str(tmp_path)))
        assert [s["name"] for s in cfg["scenarios"]] == ["s1", "s2"]

    def test_tests_dir_recognizes_jsonl_scenario(self, tmp_path):
        """数据驱动场景（cases.jsonl、无 Python 文件）同样被识别。"""
        (tmp_path / "jsonl_only").mkdir()
        (tmp_path / "jsonl_only/cases.jsonl").write_text(
            '{"id": "a", "desc": "d", "messages": [{"role": "user", "content": "hi"}],'
            ' "expect": {"nonempty": true}}\n', encoding="utf-8")
        cfg = PytestAdapter().build_config(
            make_request(datasets=[], tests_dir=str(tmp_path)))
        assert [s["name"] for s in cfg["scenarios"]] == ["jsonl_only"]

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

    def test_list_tasks_from_managed_root(self, tmp_path, monkeypatch):
        """list_tasks 枚举平台托管场景根（名字典序）。"""
        root = Path(_jsonl_root(tmp_path, {"s2": ["a"], "s1": ["b"]}))
        monkeypatch.setattr(config, "DEFAULT_FUNCTIONAL_SCENARIOS_DIR", str(root))
        tasks = PytestAdapter().list_tasks()
        assert [t["name"] for t in tasks] == ["s1", "s2"]
        assert all(t["metrics"] == ["pass_rate"] and t["tags"] == ["functional"]
                   and t["few_shot_num"] is None for t in tasks)

    def test_list_tasks_empty_when_root_missing(self, tmp_path, monkeypatch):
        """托管根不存在（还没有任何场景）→ 空清单，不报错。"""
        monkeypatch.setattr(config, "DEFAULT_FUNCTIONAL_SCENARIOS_DIR",
                            str(tmp_path / "nope"))
        assert PytestAdapter().list_tasks() == []


JUNIT_MIXED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5" failures="1" errors="1" skipped="1" time="0.1">
<testcase classname="a" name="ok"/><testcase classname="a" name="skip"><skipped/></testcase>
<testcase classname="a" name="fail"><failure>boom</failure></testcase>
<testcase classname="a" name="err"><error>bam</error></testcase>
<testcase classname="b" name="ok2"/>
</testsuite></testsuites>"""


class TestCollect:
    def _setup(self, tmp_path, name, content=JUNIT_MIXED):
        """单场景临时根 + junit/log 落档（collect 只依赖场景名与 junit）。"""
        scen = tmp_path / "ft" / name
        scen.mkdir(parents=True, exist_ok=True)
        (scen / "cases.jsonl").write_text(
            '{"id": "a", "desc": "d", "messages": [], "expect": {"nonempty": true}}\n',
            encoding="utf-8")
        p = tmp_path / "junit" / f"{name}.xml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        (tmp_path / "logs").mkdir(exist_ok=True)
        (tmp_path / "logs" / f"{name}.log").write_text("log", encoding="utf-8")

    def _req(self, tmp_path):
        return make_request(work_dir=tmp_path, tests_dir=str(tmp_path / "ft"))

    def test_pass_rate_with_failures_errors_skipped(self, tmp_path):
        self._setup(tmp_path, "s1")
        result = PytestAdapter().collect(self._req(tmp_path))
        row = result.rows[0]
        assert row.dataset == "pytest:s1" and row.metric == "pass_rate"
        assert row.value == pytest.approx(2 / 4)   # (5-1-1-1)/(5-1)
        assert row.count == 4                       # 与分母同口径
        assert row.passed is None                   # 未配阈值无判定
        assert result.raw_score == {"s1": 2 / 4}
        assert "s1.xml" in result.report_files
        assert "s1.log" in result.report_files

    def test_failures_extracted_from_junit(self, tmp_path):
        """失败/错误用例从 junit 提取为结构化详情（含断言文本，供 CLI 打印）。"""
        self._setup(tmp_path, "s1")
        result = PytestAdapter().collect(self._req(tmp_path))
        cases = {f["case"]: f for f in result.failures}
        assert set(cases) == {"fail", "err"}        # failure 与 error 都提取
        assert cases["fail"]["dataset"] == "pytest:s1"
        assert "boom" in cases["fail"]["detail"]

    def test_threshold_judgement(self, tmp_path):
        self._setup(tmp_path, "s1")
        result = PytestAdapter().collect(
            make_request(work_dir=tmp_path, tests_dir=str(tmp_path / "ft"),
                         metric_thresholds={"pass_rate": 0.6}))
        assert result.rows[0].threshold == 0.6
        assert result.rows[0].passed is False

    def test_auto_threshold_is_zero_tolerance(self, tmp_path):
        self._setup(tmp_path, "s1")
        result = PytestAdapter().collect(
            make_request(work_dir=tmp_path, tests_dir=str(tmp_path / "ft"),
                         auto_thresholds=True))
        assert result.rows[0].threshold == 1.0
        assert result.rows[0].passed is False

    def test_missing_junit_raises(self, tmp_path):
        self._setup(tmp_path, "s1")
        (tmp_path / "junit" / "s1.xml").unlink()
        with pytest.raises(RuntimeError, match="junit"):
            PytestAdapter().collect(self._req(tmp_path))

    def test_zero_tests_raises(self, tmp_path):
        self._setup(tmp_path, "s1",
                    '<testsuites><testsuite name="pytest" tests="0" failures="0"'
                    ' errors="0" skipped="0"/></testsuites>')
        with pytest.raises(RuntimeError, match="未收集到用例"):
            PytestAdapter().collect(self._req(tmp_path))


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


class _FakeModelHandler(BaseHTTPRequestHandler):
    """固定回复的 chat/completions 端点（离线验证 cases.jsonl 全链路）。"""

    def do_POST(self):
        body = json.dumps({"choices": [{"message": {"content": "hello from fake"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def test_run_jsonl_scenario_against_fake_server(tmp_path):
    """数据驱动场景（cases.jsonl、零 Python）端到端：收集→请求→断言→pass_rate。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeModelHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = make_request(
            datasets=[], tests_dir=str(FT_JSONL), work_dir=tmp_path,
            target={"type": "openai_compatible",
                    "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
                    "model": "fake-model"},
            metric_thresholds={"pass_rate": 0.6})
        result = await PytestAdapter().run(req)
    finally:
        server.shutdown()
    row = result.rows[0]
    assert row.dataset == "pytest:smoke" and row.count == 2
    assert row.value == pytest.approx(0.5)             # smoke_fail 确定性失败
    assert row.passed is False
    assert result.raw_score == {"smoke": 0.5}
    log = (tmp_path / "logs" / "smoke.log").read_text(encoding="utf-8")
    assert "smoke_fail" in log and "goodbye" in log    # 失败详情与 desc 留档


async def test_run_cases_filter_end_to_end(tmp_path):
    """tools.cases 单用例复现端到端：只跑指定 id，pass_rate 反映被筛选的用例集。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeModelHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = make_request(
            datasets=[], tests_dir=str(FT_JSONL), work_dir=tmp_path,
            cases=["smoke_pass"],
            target={"type": "openai_compatible",
                    "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
                    "model": "fake-model"})
        result = await PytestAdapter().run(req)
    finally:
        server.shutdown()
    row = result.rows[0]
    assert row.count == 1                              # 只跑了 smoke_pass 这 1 例
    assert row.value == 1.0 and row.passed is None     # 未配阈值无判定
    log = (tmp_path / "logs" / "smoke.log").read_text(encoding="utf-8")
    assert "1 passed" in log and "smoke_fail" not in log    # 未选中的用例不执行


async def test_run_repeat_case_end_to_end(tmp_path):
    """repeat 用例端到端：一行 repeat=2 → 两轮独立执行、两条记录（id / id[r2]）。"""
    root = tmp_path / "ft"
    d = root / "smoke"
    d.mkdir(parents=True)
    (d / "cases.jsonl").write_text(json.dumps(
        {"id": "a", "desc": "d", "repeat": 2,
         "messages": [{"role": "user", "content": "hi"}],
         "expect": {"contains": "hello"}}) + "\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeModelHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = make_request(
            datasets=[], tests_dir=str(root), work_dir=tmp_path,
            target={"type": "openai_compatible",
                    "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
                    "model": "fake-model"})
        result = await PytestAdapter().run(req)
    finally:
        server.shutdown()
    row = result.rows[0]
    assert row.count == 2 and row.value == 1.0         # 两轮各计一例，全过
    records = [json.loads(x) for x in
               (tmp_path / "results" / "smoke.jsonl").read_text(encoding="utf-8").splitlines()
               if x.strip()]
    assert [r["external_key"] for r in records] == ["a", "a[r2]"]   # 按轮次各记一条
    assert all(r["passed"] for r in records)
