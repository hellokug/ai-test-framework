"""工具适配层单测（全离线：不 import evalscope、不触网）。"""

import json
import pathlib

import pytest

from aite.core import profiles
from aite.tools import (
    normalize_resume_mode,
    parse_tool_suite,
    run_tool_suite,
    tool_identity_hash,
)
from aite.tools.base import ToolAdapter, ToolMetricRow, ToolRunRequest, ToolRunResult, get_tool
from aite.tools.evalscope_adapter import EvalscopeAdapter, _normalize_metric_names
from aite.tools.subproc import kill_tree


def test_normalize_metric_names():
    assert _normalize_metric_names(["acc"]) == ["mean_acc"]
    assert _normalize_metric_names([{"acc": {"numeric": True}}]) == ["mean_acc"]
    assert _normalize_metric_names(["acc", "rouge"]) == ["mean_acc", "rouge"]
    assert _normalize_metric_names([]) == []


def make_request(**kw):
    base = dict(
        run_name="t",
        target={"type": "openai_compatible", "base_url": "http://localhost:11434/v1",
                "model": "qwen2.5:3b"},
        datasets=["ceval"],
    )
    base.update(kw)
    return ToolRunRequest(**base)


def write_report(work_dir, filename, payload):
    """filename 可含子目录（no_timestamp 模式为 reports/<model>/<ds>.json）。"""
    path = work_dir / "reports" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestEvalscopeAdapter:
    def test_build_config_maps_target(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        cfg = EvalscopeAdapter().build_config(make_request(limit_per_subset=2))
        assert cfg["api_url"] == "http://localhost:11434/v1/chat/completions"
        assert cfg["api_key"] == "sk-test"
        assert cfg["eval_type"] == "openai_api"
        assert cfg["model"] == "qwen2.5:3b"
        assert cfg["datasets"] == ["ceval"]
        assert cfg["limit"] == 2            # 每科目条数语义直接映射
        assert cfg["no_timestamp"] is True  # 时间戳由 aite 侧目录承担

    def test_build_config_extra_passthrough(self):
        req = make_request(extra={"eval_backend": "OpenCompass"})
        cfg = EvalscopeAdapter().build_config(req)
        assert cfg["eval_backend"] == "OpenCompass"

    def test_build_config_rejects_non_openai_target(self):
        req = make_request(target={"type": "anthropic"})
        with pytest.raises(ValueError, match="openai_compatible"):
            EvalscopeAdapter().build_config(req)

    def test_collect_parses_report_json(self, tmp_path):
        # no_timestamp 模式的实际布局：reports/<model>/<ds>.json
        write_report(tmp_path, "qwen2.5_3b/gsm8k.json", {
            "dataset_name": "gsm8k", "score": 0.6859,
            "metrics": [{"name": "mean_acc", "num": 156, "score": 0.6859}],
            "perf_metrics": {"summary": {"n_samples": 156}},
        })
        req = make_request(work_dir=tmp_path, metric_thresholds={"mean_acc": 0.5})
        result = EvalscopeAdapter().collect(req)
        assert result.raw_score == {"gsm8k": 0.6859}
        row = result.rows[0]
        assert row.dataset == "evalscope:gsm8k"
        assert row.metric == "mean_acc" and row.count == 156
        assert row.threshold == 0.5 and row.passed is True
        assert "gsm8k.json" in result.report_files
        assert result.perf["gsm8k"]["n_samples"] == 156

    def test_collect_no_threshold_passes_none(self, tmp_path):
        write_report(tmp_path, "m@gsm8k.json", {
            "dataset_name": "gsm8k", "score": 0.1,
            "metrics": [{"name": "mean_acc", "num": 3, "score": 0.1}],
        })
        result = EvalscopeAdapter().collect(make_request(work_dir=tmp_path))
        assert result.rows[0].passed is None

    def test_collect_skips_duplicate_dataset(self, tmp_path):
        payload = {"dataset_name": "gsm8k", "score": 0.2,
                   "metrics": [{"name": "mean_acc", "num": 1, "score": 0.2}]}
        write_report(tmp_path, "a@gsm8k.json", payload)
        write_report(tmp_path, "b@gsm8k.json", payload)
        result = EvalscopeAdapter().collect(make_request(work_dir=tmp_path))
        assert len(result.rows) == 1

    def test_auto_thresholds_choice_baseline(self, tmp_path):
        write_report(tmp_path, "m@mmlu.json", {
            "dataset_name": "mmlu", "score": 0.3,
            "metrics": [{"name": "mean_acc", "num": 10, "score": 0.3}],
        })
        result = EvalscopeAdapter().collect(
            make_request(work_dir=tmp_path, auto_thresholds=True))
        assert result.rows[0].threshold == 0.25   # 4 选 1 随机基线
        assert result.rows[0].passed is True      # 0.3 >= 0.25

    def test_auto_thresholds_open_ended_no_default(self, tmp_path):
        write_report(tmp_path, "m@gsm8k.json", {
            "dataset_name": "gsm8k", "score": 0.2,
            "metrics": [{"name": "mean_acc", "num": 1, "score": 0.2}],
        })
        result = EvalscopeAdapter().collect(
            make_request(work_dir=tmp_path, auto_thresholds=True))
        assert result.rows[0].threshold is None    # 开放题不给默认
        assert result.rows[0].passed is None

    def test_explicit_threshold_wins_over_auto(self, tmp_path):
        write_report(tmp_path, "m@mmlu.json", {
            "dataset_name": "mmlu", "score": 0.3,
            "metrics": [{"name": "mean_acc", "num": 10, "score": 0.3}],
        })
        req = make_request(work_dir=tmp_path, auto_thresholds=True,
                           metric_thresholds={"mean_acc": 0.5})
        result = EvalscopeAdapter().collect(req)
        assert result.rows[0].threshold == 0.5     # 显式优先
        assert result.rows[0].passed is False

    def test_collect_threshold_alias_acc_vs_mean_acc(self, tmp_path):
        """阈值键容错：报告原始名 acc ↔ 配置键 mean_acc 双向命中，不再静默不判定。"""
        write_report(tmp_path, "m@gsm8k.json", {
            "dataset_name": "gsm8k",
            "metrics": [{"name": "acc", "num": 10, "score": 0.8}]})
        result = EvalscopeAdapter().collect(
            make_request(work_dir=tmp_path, metric_thresholds={"mean_acc": 0.5}))
        assert result.rows[0].threshold == 0.5 and result.rows[0].passed is True

        write_report(tmp_path, "m@arc.json", {
            "dataset_name": "arc",
            "metrics": [{"name": "mean_acc", "num": 10, "score": 0.2}]})
        result = EvalscopeAdapter().collect(
            make_request(work_dir=tmp_path, metric_thresholds={"acc": 0.5}))
        row = next(r for r in result.rows if r.dataset == "evalscope:arc")
        assert row.threshold == 0.5 and row.passed is False

    def test_auto_threshold_hits_report_raw_name_acc(self, tmp_path):
        """auto 阈值：推荐表键 mean_acc，报告若报原始名 acc 亦命中。"""
        write_report(tmp_path, "m@mmlu.json", {
            "dataset_name": "mmlu",
            "metrics": [{"name": "acc", "num": 10, "score": 0.3}]})
        result = EvalscopeAdapter().collect(
            make_request(work_dir=tmp_path, auto_thresholds=True))
        assert result.rows[0].threshold == 0.25 and result.rows[0].passed is True

    def test_tasks_from_stdout_skips_banner(self):
        """独立环境列举的 stdout 解析：banner 行混入时取末段 JSON 数组。"""
        from aite.tools.evalscope_adapter import _tasks_from_stdout

        stdout = "evalscope 1.9.1\nloading...\n[{\"name\": \"mmlu\", \"metrics\": [\"mean_acc\"]}]"
        assert _tasks_from_stdout(stdout)[0]["name"] == "mmlu"
        with pytest.raises(RuntimeError, match="JSON"):
            _tasks_from_stdout("no json here")

    def test_build_config_judge_prefab(self, monkeypatch):
        """tools.judge 预制 → 原生 typed judge 结构（model_id/api_url/api_key）。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-judge")
        req = make_request(judge={"base_url": "https://api.deepseek.com/v1",
                                  "model": "deepseek-reasoner",
                                  "api_key_env": "DEEPSEEK_API_KEY"})
        cfg = EvalscopeAdapter().build_config(req)
        jmodel = cfg["judge"]["models"][0]
        assert jmodel["model_id"] == "deepseek-reasoner"
        assert jmodel["api_url"] == "https://api.deepseek.com/v1/chat/completions"
        assert jmodel["api_key"] == "sk-judge"

    def test_build_config_judge_requires_endpoint(self):
        req = make_request(judge={"model": "deepseek-reasoner"})
        with pytest.raises(ValueError, match="base_url"):
            EvalscopeAdapter().build_config(req)

    def test_build_config_judge_conflicts_with_native(self):
        """预制引用与 extra_args 原生透传二选一，不允许静默覆盖。"""
        req = make_request(judge={"base_url": "http://x/v1", "model": "j"},
                           extra={"judge": {"models": []}})
        with pytest.raises(ValueError, match="二选一"):
            EvalscopeAdapter().build_config(req)

    def test_build_config_native_judge_passthrough(self):
        """高级形态：extra_args.judge 直写原生结构原样透传。"""
        req = make_request(extra={"judge": {"strategy": "rule"}})
        assert EvalscopeAdapter().build_config(req)["judge"] == {"strategy": "rule"}

    def test_build_config_judge_contract_semantics(self):
        """tools.judge 语义契约键 → 原生 judge.contract（前端评分标准 rubric 走此映射）。"""
        req = make_request(judge={"base_url": "http://x/v1", "model": "j",
                                  "prompt_template": "按 rubric 评分：{question} {pred} {gold}"})
        cfg = EvalscopeAdapter().build_config(req)
        assert cfg["judge"]["contract"] == {
            "prompt_template": "按 rubric 评分：{question} {pred} {gold}"}

    def test_build_config_judge_no_contract_by_default(self):
        """不配语义键时原生 judge 不带 contract（保持官方默认评分提示词）。"""
        req = make_request(judge={"base_url": "http://x/v1", "model": "j"})
        assert "contract" not in EvalscopeAdapter().build_config(req)["judge"]

    def test_dispatch_line_splits_progress_and_log_mirror(self):
        """日志行分流：进度模式驱动进度条，其余行镜像为 log 事件（空行丢、长行截断）。"""
        from aite.tools.evalscope_adapter import _dispatch_line

        seen = []
        _dispatch_line("Running[task]  50%|  5/10", seen.append)
        assert seen[-1]["type"] == "progress" and seen[-1]["done"] == 5
        _dispatch_line("Start loading benchmark dataset", seen.append)
        assert seen[-1]["type"] == "progress" and "加载数据集" in seen[-1]["phase"]
        _dispatch_line("Downloading dataset shard 3/10", seen.append)
        assert seen[-1] == {"type": "log", "line": "Downloading dataset shard 3/10"}
        _dispatch_line("x" * 500, seen.append)
        assert seen[-1]["type"] == "log" and len(seen[-1]["line"]) == 300
        before = len(seen)
        _dispatch_line("   ", seen.append)
        assert len(seen) == before                        # 空行不产生事件

    def test_build_config_resume_sets_use_cache(self):
        """断点续推：use_cache 指向工作目录（evalscope 就地复用预测/判分）。"""
        req = make_request(resume=True, work_dir=pathlib.Path("/w/aa_lcr/20260929-120000"))
        cfg = EvalscopeAdapter().build_config(req)
        assert cfg["use_cache"] == str(req.work_dir)
        assert EvalscopeAdapter().build_config(make_request()).get("use_cache") is None

    def test_subprocess_cfg_paths_absolutized(self):
        """use_cache 与 work_dir 一起强制绝对路径：子进程 cwd 已切进目录，相对
        路径会在其下二次拼接（2026-09-29 套娃实弹：产物整套嵌套一层）。"""
        cfg = {"work_dir": "reports/tool/x", "use_cache": "reports/tool/x"}
        EvalscopeAdapter._normalize_cfg_paths(cfg, pathlib.Path("/abs/run"))
        assert cfg["work_dir"].replace("\\", "/") == "/abs/run"
        assert cfg["use_cache"].replace("\\", "/") == "/abs/run"
        cfg2 = {"work_dir": "w"}
        EvalscopeAdapter._normalize_cfg_paths(cfg2, pathlib.Path("/abs/run"))
        assert "use_cache" not in cfg2

    def test_resume_identity_conflict_auto_fallback(self):
        """续推撞上身份校验不符（上次目录无快照）→ 自动 rerun_review 重试一次。"""
        import asyncio

        adapter = EvalscopeAdapter()
        calls = []

        async def fake_impl(req, exe):
            calls.append(dict(req.extra))
            if len(calls) == 1:
                raise RuntimeError("evalscope task failed: ValueError: Cached evaluation "
                                   "identity does not match the current run "
                                   "(previous=unknown, current=sha256:abc)")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(adapter, "_run_subprocess_impl", fake_impl)
        monkeypatch.setattr(adapter, "collect", lambda req: "collected")
        try:
            result = asyncio.run(adapter._run_subprocess(make_request(resume=True), "py"))
            assert result == "collected"
            assert "rerun_review" not in calls[0] and calls[1]["rerun_review"] is True
        finally:
            monkeypatch.undo()

    def test_resume_identity_conflict_requires_resume_flag(self):
        """非续推场景的同类报错不兜底（直接抛出）。"""
        import asyncio

        adapter = EvalscopeAdapter()

        async def fake_impl(req, exe):
            raise RuntimeError("evalscope task failed: Cached evaluation identity "
                               "does not match the current run")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(adapter, "_run_subprocess_impl", fake_impl)
        try:
            with pytest.raises(RuntimeError, match="identity"):
                asyncio.run(adapter._run_subprocess(make_request(), "py"))
        finally:
            monkeypatch.undo()

    def test_is_identity_conflict_matcher(self):
        from aite.tools.evalscope_adapter import _is_identity_conflict

        assert _is_identity_conflict("ValueError: Cached evaluation identity does "
                                     "not match the current run (previous=unknown)")
        assert not _is_identity_conflict("Error code: 402 - Insufficient Balance")

    def test_bootstrap_scripts_are_valid_python(self):
        """引导串虽不执行也必须是合法 Python——缩进坏了子进程秒炸
        IndentationError（实弹教训 2026-09-28：离线单测全绿拦不住它）。"""
        import ast

        for src in (EvalscopeAdapter.RUN_TASK_BOOTSTRAP,
                    EvalscopeAdapter.LIST_TASKS_BOOTSTRAP):
            ast.parse(src)


class TestKillTree:
    def test_kill_tree_terminates_process(self):
        import subprocess
        import sys

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        kill_tree(proc)
        assert proc.wait(timeout=15) is not None


class TestToolSuiteParsing:
    def test_parse_requires_datasets(self):
        with pytest.raises(ValueError, match="tools.datasets"):
            parse_tool_suite({"engine": "evalscope", "tools": {}})

    def test_parse_extracts_tools_judge(self, monkeypatch, tmp_path):
        # 隔离本地 data/common.yaml（真实环境可能已注册同名预制，展开会改变断言对象）
        monkeypatch.setattr(profiles, "common_path", lambda: tmp_path / "nope.yaml")
        req, _ = parse_tool_suite({"engine": "evalscope",
                                   "tools": {"datasets": ["aime26"],
                                             "judge": {"profile": "deepseek"}}})
        assert req.judge == {"profile": "deepseek"}

    def test_parse_rejects_non_dict_judge(self, monkeypatch, tmp_path):
        monkeypatch.setattr(profiles, "common_path", lambda: tmp_path / "nope.yaml")
        with pytest.raises(ValueError, match="tools.judge"):
            parse_tool_suite({"engine": "evalscope",
                              "tools": {"datasets": ["aime26"], "judge": "deepseek"}})

    def test_parse_resume_reuses_latest_dir(self, tmp_path):
        """断点续推：resume=true 复用最新历史输出目录（时间戳名排序即时序）。"""
        base = tmp_path / "reports" / "tool" / "evalscope" / "aa_lcr"
        base.mkdir(parents=True)
        for stamp in ("20260928-100000", "20260929-120000"):
            (base / stamp).mkdir()
        req, _ = parse_tool_suite({"engine": "evalscope", "run_name": "aa_lcr",
                                   "output_dir": str(tmp_path / "reports"),
                                   "tools": {"datasets": ["aa_lcr"], "resume": True}})
        assert req.resume is True
        assert req.work_dir == base / "20260929-120000"

    def test_parse_resume_without_history_starts_fresh(self, tmp_path):
        req, _ = parse_tool_suite({"engine": "evalscope", "run_name": "aa_lcr",
                                   "output_dir": str(tmp_path / "reports"),
                                   "tools": {"datasets": ["aa_lcr"], "resume": True}})
        assert req.resume is True
        assert req.work_dir.parent.name == "aa_lcr"      # 新时间戳目录照常创建
        assert len(req.work_dir.name) == 15

    def test_parse_resume_default_auto_fresh_without_state(self, tmp_path):
        """缺省 auto：历史目录无 run_state.json（旧版产物/被删）→ 不赌身份，全新起跑。"""
        base = tmp_path / "reports" / "tool" / "evalscope" / "aa_lcr"
        (base / "20260928-100000").mkdir(parents=True)
        req, _ = parse_tool_suite({"engine": "evalscope", "output_dir": str(tmp_path / "reports"),
                                   "tools": {"datasets": ["aa_lcr"]}})
        assert req.resume is True            # auto 档仍武装 use_cache（身份快照照写）
        assert req.work_dir != base / "20260928-100000"

    def test_parse_tests_dir_allows_empty_datasets(self):
        req, _ = parse_tool_suite({"engine": "pytest", "tools": {"tests_dir": "my_ft"}})
        assert req.tests_dir == "my_ft"
        assert req.datasets == []

    def test_parse_perf_section_allows_empty_datasets(self):
        raw = {"engine": "evalscope-perf", "perf": {"prompt": "p", "parallel": [1]}}
        req, _ = parse_tool_suite(raw)
        assert req.perf == {"prompt": "p", "parallel": [1]}
        assert req.datasets == []

    def test_parse_rejects_bad_perf_section(self):
        with pytest.raises(ValueError, match="perf 段"):
            parse_tool_suite({"engine": "evalscope-perf", "perf": "oops"})

    def test_parse_without_tests_dir_keeps_datasets(self):
        req, _ = parse_tool_suite({"engine": "pytest", "tools": {"datasets": ["basic_chat"]}})
        assert req.tests_dir is None
        assert req.datasets == ["basic_chat"]

    def test_parse_cases_filter(self):
        req, _ = parse_tool_suite({"engine": "pytest", "tools": {
            "datasets": ["basic_chat"], "cases": ["yes_no"]}})
        assert req.cases == ["yes_no"]

    def test_parse_rejects_bad_cases(self):
        with pytest.raises(ValueError, match="tools.cases"):
            parse_tool_suite({"engine": "pytest", "tools": {
                "datasets": ["basic_chat"], "cases": "yes_no"}})

    def test_unknown_engine(self):
        with pytest.raises(KeyError, match="unknown tool engine"):
            get_tool("no_such_tool")


async def test_tool_suite_total_cases_sums_first_metric_group(repo, monkeypatch):
    """total_cases 回退 = 首个 metric 组 count 总和（多数据集求和，非首行）。"""
    from aite.tools import run_tool_suite
    from aite.tools.base import ToolMetricRow, ToolRunResult
    from aite.tools.evalscope_adapter import EvalscopeAdapter

    async def fake_run(self, req):
        return ToolRunResult(rows=[
            ToolMetricRow(dataset="evalscope:ceval", metric="mean_acc",
                          value=0.5, count=6),
            ToolMetricRow(dataset="evalscope:mmlu", metric="mean_acc",
                          value=1.0, count=4),
        ])

    monkeypatch.setattr(EvalscopeAdapter, "run", fake_run)
    raw = {"engine": "evalscope", "run_name": "t",
           "target": {"type": "openai_compatible", "base_url": "http://x/v1", "model": "m"},
           "tools": {"datasets": ["ceval", "mmlu"]}}
    report = await run_tool_suite(raw, repo)
    assert report.status == "passed"
    assert report.total_cases == 10   # 6 + 4，而非 rows[0].count=6


async def test_tool_suite_overall_row(repo, tmp_path, monkeypatch):
    """工具 run 必须产出 overall 行（dataset=None），CLI 汇总表只显示 overall。"""
    from aite.tools import run_tool_suite
    from aite.tools.base import ToolMetricRow, ToolRunResult
    from aite.tools.evalscope_adapter import EvalscopeAdapter

    async def fake_run(self, req):
        return ToolRunResult(rows=[
            ToolMetricRow(dataset="evalscope:gsm8k", metric="mean_acc",
                          value=1.0, count=2, threshold=0.3, passed=True),
        ])

    monkeypatch.setattr(EvalscopeAdapter, "run", fake_run)
    raw = {"engine": "evalscope", "run_name": "t",
           "target": {"type": "openai_compatible", "base_url": "http://x/v1", "model": "m"},
           "tools": {"datasets": ["gsm8k"], "metric_thresholds": {"mean_acc": 0.3}}}
    report = await run_tool_suite(raw, repo)
    assert report.status == "passed"
    overall = report.summary_for("mean_acc")
    assert overall is not None and overall.dataset is None
    assert overall.value == 1.0 and overall.count == 2
    # 分组行与 overall 行都入库
    assert len(report.summaries) == 2


async def test_tool_suite_carries_failure_details(repo, monkeypatch):
    """适配器提取的用例级失败详情进 report.details（CLI 打印用）。"""
    from aite.tools import run_tool_suite
    from aite.tools.base import ToolMetricRow, ToolRunResult
    from aite.tools.evalscope_adapter import EvalscopeAdapter

    async def fake_run(self, req):
        return ToolRunResult(
            rows=[ToolMetricRow(dataset="pytest:basic_chat", metric="pass_rate",
                                value=0.0, count=1)],
            failures=[{"dataset": "pytest:basic_chat", "case": "yes_no",
                       "detail": "[yes_no] 断言失败: 期望包含 'yes'"}],
        )

    monkeypatch.setattr(EvalscopeAdapter, "run", fake_run)
    raw = {"engine": "evalscope", "run_name": "t",
           "target": {"type": "openai_compatible", "base_url": "http://x/v1", "model": "m"},
           "tools": {"datasets": ["gsm8k"]}}
    report = await run_tool_suite(raw, repo)
    assert report.details["failures"][0]["case"] == "yes_no"


class TestEvalscopeSubprocess:
    """独立环境执行模式：python_exe 指向 evalscope 专属 venv（py3.14 装不上
    editdistance 实证），配置走文件契约、结果走既有 reports/*.json 收集。"""

    def _req(self, tmp_path, extra=None):
        from aite.tools.base import ToolRunRequest
        return ToolRunRequest(
            run_name="swe-eval", target={"model": "qwen2.5:3b",
                                         "base_url": "http://localhost:11434/v1",
                                         "api_key_env": "OLLAMA_KEY"},
            datasets=["swe_bench_lite_agentic"], limit_per_subset=1,
            work_dir=tmp_path, extra=extra or {})

    def test_build_config_hides_reserved_keys(self, tmp_path):
        """python_exe/timeout_s 是适配器保留键，不得泄漏进 TaskConfig。"""
        from aite.tools.evalscope_adapter import EvalscopeAdapter
        cfg = EvalscopeAdapter().build_config(
            self._req(tmp_path, {"python_exe": "./.venv-evalscope/Scripts/python.exe",
                                 "timeout_s": 14400, "use_cache": False}))
        assert cfg["model"] == "qwen2.5:3b"
        assert cfg["datasets"] == ["swe_bench_lite_agentic"]
        assert "python_exe" not in cfg and "timeout_s" not in cfg

    @pytest.mark.asyncio
    async def test_run_via_subprocess_writes_and_collects(self, tmp_path, monkeypatch):
        import json as _json

        from aite.tools import evalscope_adapter as em

        captured = {}

        class FakePopen:
            def __init__(self, cmd, **kw):
                captured["cmd"] = cmd
                self.returncode = None
                assert pathlib.Path(cmd[3]).is_absolute(), "cwd 切换后相对路径会错位"
                cfg = _json.loads(pathlib.Path(cmd[3]).read_text(encoding="utf-8"))
                assert cfg["datasets"] == ["swe_bench_lite_agentic"]
                assert pathlib.Path(cfg["work_dir"]).is_absolute()
                assert kw["env"]["PYTHONUTF8"] == "1"   # 中文 Windows GBK 解码防御
                assert kw["env"]["HF_ENDPOINT"] == "https://hf-mirror.com"   # 默认镜像
                # HF_HOME 注入：运行时自动下载随数据集设置 cache_dir 落位（绝对路径）
                from aite import config as _config
                assert kw["env"]["HF_HOME"] == str(
                    pathlib.Path(_config.DEFAULT_DATASET_CACHE_DIR).resolve())
                reports = pathlib.Path(kw["cwd"]) / "reports"
                reports.mkdir(parents=True, exist_ok=True)
                (reports / "qwen2.5_3b.json").write_text(_json.dumps({
                    "dataset_name": "swe_bench_lite_agentic", "score": 0.25,
                    "metrics": [{"name": "acc", "num": 1, "score": 0.25}],
                }, ensure_ascii=False), encoding="utf-8")
                stderr = pathlib.Path(kw["cwd"]) / "proc_stderr.log"
                stderr.write_text("ok", encoding="utf-8")
                self.returncode = 0   # 立即退出

            def poll(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        # HF_HOME 断言去环境化：屏蔽本机 HF_HOME，缓存根钉在 config 默认
        monkeypatch.delenv("HF_HOME", raising=False)
        from aite import config as _acc_config
        monkeypatch.setattr("aite.datasets.settings.resolve_cache_dir",
                            lambda: pathlib.Path(_acc_config.DEFAULT_DATASET_CACHE_DIR))
        monkeypatch.setattr(em.subprocess, "Popen", FakePopen)
        result = await em.EvalscopeAdapter().run(self._req(
            tmp_path, {"python_exe": "py310-python", "timeout_s": 3600}))
        cmd = captured["cmd"]
        assert cmd[0] == "py310-python" and "-c" in cmd[:3]
        assert any("run_task" in c for c in cmd)
        assert result.raw_score["swe_bench_lite_agentic"] == 0.25
        assert result.rows[0].dataset == "evalscope:swe_bench_lite_agentic"

    @pytest.mark.asyncio
    async def test_subprocess_failure_raises_with_tail(self, tmp_path, monkeypatch):
        from aite.tools import evalscope_adapter as em

        class FailPopen:
            def __init__(self, cmd, **kw):
                import pathlib
                self.returncode = None
                stderr = pathlib.Path(kw["cwd"]) / "proc_stderr.log"
                stderr.parent.mkdir(parents=True, exist_ok=True)
                stderr.write_text("boom: sandbox image pull denied", encoding="utf-8")
                self.returncode = 1

            def poll(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        monkeypatch.setattr(em.subprocess, "Popen", FailPopen)
        with pytest.raises(RuntimeError, match="sandbox image pull denied"):
            await em.EvalscopeAdapter().run(self._req(
                tmp_path, {"python_exe": "py310-python"}))

    @pytest.mark.asyncio
    async def test_in_process_fallback_when_no_python_exe(self, tmp_path, monkeypatch):
        """无 python_exe 时维持原进程内路径（evalscope 可导入的环境）。"""
        import sys
        import types

        from aite.tools import evalscope_adapter as em

        called = {}

        def fake_run_task(cfg):
            called["cfg"] = cfg

        stub = types.SimpleNamespace(run_task=fake_run_task)
        monkeypatch.setitem(sys.modules, "evalscope", stub)
        await em.EvalscopeAdapter().run(self._req(tmp_path))
        assert called["cfg"]["model"] == "qwen2.5:3b"


class TestProgressParsing:
    """evalscope 日志进度解析 + 事件回传（前端/CLI 运行期可见性）。"""

    def test_parse_progress_lines(self):
        from aite.tools.evalscope_adapter import parse_progress
        lines = [
            "2026-09-23 22:25:16 - evalscope - INFO: Evaluating[swe_bench_lite_agentic] 100%| 1/1 [Elapsed: 00:00]",
            "2026-09-23 22:25:16 - evalscope - INFO: Running[eval]   0%| 0/1 [Elapsed: 08:56]",
            "2026-09-23 22:25:16 - evalscope - INFO: Start loading benchmark dataset",
        ]
        events = [ev for line in lines if (ev := parse_progress(line))]
        assert events == [
            {"type": "progress", "phase": "Evaluating[swe_bench_lite_agentic]",
             "done": 1, "total": 1},
            {"type": "progress", "phase": "Running[eval]", "done": 0, "total": 1},
            {"type": "progress", "phase": "加载数据集"},   # 阶段行：仅状态播报，无计数
        ]

    @pytest.mark.asyncio
    async def test_run_tool_suite_forwards_progress_events(self, tmp_path, monkeypatch):
        """event_cb 收到 progress 事件；total 已知即回写 run 行 total_cases。"""
        from aite.tools import run_tool_suite
        from aite.tools.base import ToolMetricRow, ToolRunResult

        raw = {"engine": "fake-progress", "run_name": "p1",
               "target": {"model": "m"},
               "tools": {"datasets": ["d1"]},
               "storage": {"url": "sqlite+aiosqlite://"}}

        class FakeAdapter:
            def build_config(self, req):
                return {}

            async def run(self, req):
                assert req.progress_cb is not None, "适配器应拿到进度回调"
                req.progress_cb({"type": "progress", "phase": "Evaluating[d1]",
                                 "done": 0, "total": 3})
                return ToolRunResult(rows=[ToolMetricRow(
                    dataset="evalscope:d1", metric="acc", value=1.0, count=3)])

        from aite.tools.base import register_tool, unregister_tool
        register_tool("fake-progress")(FakeAdapter)

        class FakeRepo:
            def __init__(self):
                self.updates = []

            async def upsert_suite(self, name, cfg):
                return "s1"

            async def create_run(self, *a, **k):
                return "r1"

            async def update_run(self, run_id, **fields):
                self.updates.append(fields)

            async def list_metric_summaries(self, run_id):
                return []

            async def update_case_results(self, *a, **k):
                return None

        repo = FakeRepo()
        events = []
        await run_tool_suite(raw, repo, event_cb=events.append)
        assert {"type": "progress", "phase": "Evaluating[d1]",
                "done": 0, "total": 3} in events
        assert any(u.get("total_cases") == 3 for u in repo.updates)
        unregister_tool("fake-progress")

    def test_collect_unnamed_metric_fallback(self, tmp_path):
        """swe_bench agentic 报告：metrics 无名 + 顶层 score 缺失 →
        兜底命名 score 并回填 raw_score（实弹 20260923 报告形态）。"""
        import json as _json

        from aite.tools.base import ToolRunRequest
        from aite.tools.evalscope_adapter import EvalscopeAdapter

        reports = tmp_path / "reports" / "deepseek-chat"
        reports.mkdir(parents=True)
        (reports / "swe_bench_lite_agentic.json").write_text(_json.dumps({
            "schema_version": 2, "dataset_name": "swe_bench_lite_agentic",
            "score": None,
            "metrics": [{"name": None, "num": 1, "score": 0.0}],
        }), encoding="utf-8")
        req = ToolRunRequest(run_name="r", target={}, datasets=[],
                             work_dir=tmp_path)
        result = EvalscopeAdapter().collect(req)
        assert result.rows[0].metric == "score"
        assert result.rows[0].value == 0.0
        assert result.raw_score["swe_bench_lite_agentic"] == 0.0

    def test_parse_stage_lines(self):
        """阶段行（无计数）→ 仅 phase 的状态事件（运行期"在干嘛"可见性）。"""
        from aite.tools.evalscope_adapter import parse_progress
        cases = [
            ("...INFO: Start loading benchmark dataset: swe_bench_lite_agentic", "加载数据集"),
            ("...INFO: Attempting to pull 1 SWE-Bench images from Docker Hub", "拉取实例镜像 ×1"),
            ("...INFO: SandboxService: manager started for engine=docker", "沙箱已启动"),
        ]
        for line, phase in cases:
            ev = parse_progress(line)
            assert ev and ev["phase"] == phase and "total" not in ev

    def test_parse_pull_progress(self):
        from aite.tools.evalscope_adapter import parse_progress
        ev = parse_progress("...INFO: Pulling SWE-Bench images   0%| 0/1 [Elapsed: 01:00]")
        assert ev == {"type": "progress", "phase": "拉取实例镜像", "done": 0, "total": 1}


class TestResumePolicy:
    """resume 三态决策矩阵（缺省 auto）：run_state.json 完成态 + 生成侧身份。"""

    def _raw(self, tmp_path, **tools):
        tools.setdefault("datasets", ["aa_lcr"])
        return {"engine": "evalscope", "run_name": "aa_lcr",
                "output_dir": str(tmp_path / "reports"), "tools": tools}

    def _seed(self, tmp_path, stamp, raw=None, status="running"):
        """播种历史目录；raw 给定时写入与该配置匹配的身份哈希。"""
        d = tmp_path / "reports" / "tool" / "evalscope" / "aa_lcr" / stamp
        d.mkdir(parents=True)
        if raw is not None:
            (d / "run_state.json").write_text(json.dumps({
                "status": status, "identity_hash": tool_identity_hash(raw),
            }, ensure_ascii=False), encoding="utf-8")
        return d

    def test_auto_resumes_incomplete(self, tmp_path, monkeypatch):
        """auto + 上次未完成 + 身份一致 → 就地续推最近目录。"""
        monkeypatch.setattr(profiles, "common_path", lambda: tmp_path / "nope.yaml")
        raw = self._raw(tmp_path)
        seeded = self._seed(tmp_path, "20260929-120000", raw, status="running")
        req, _ = parse_tool_suite(raw)
        assert req.resume is True and req.work_dir == seeded

    def test_auto_starts_fresh_when_completed(self, tmp_path, monkeypatch):
        """auto + 上次已完成 → 全新起跑（跑完的任务再跑 = 新任务）。"""
        monkeypatch.setattr(profiles, "common_path", lambda: tmp_path / "nope.yaml")
        raw = self._raw(tmp_path)
        seeded = self._seed(tmp_path, "20260929-120000", raw, status="completed")
        req, _ = parse_tool_suite(raw)
        assert req.work_dir != seeded and req.work_dir.parent == seeded.parent

    def test_force_reuses_completed_dir(self, tmp_path, monkeypatch):
        """force（resume: true）+ 已完成 → 照样复用（改判官重判/重出报告场景）。"""
        monkeypatch.setattr(profiles, "common_path", lambda: tmp_path / "nope.yaml")
        raw = self._raw(tmp_path, resume=True)
        seeded = self._seed(tmp_path, "20260929-120000", raw, status="completed")
        req, _ = parse_tool_suite(raw)
        assert req.work_dir == seeded

    def test_identity_change_forces_fresh_even_if_incomplete(self, tmp_path, monkeypatch):
        """身份变更（limit 5 → 全量）→ 未完成也不续推：不混入不可比预测。"""
        monkeypatch.setattr(profiles, "common_path", lambda: tmp_path / "nope.yaml")
        seeded_raw = self._raw(tmp_path, limit_per_subset=5)
        seeded = self._seed(tmp_path, "20260929-120000", seeded_raw, status="running")
        raw = self._raw(tmp_path, resume=True)   # force 也压不过身份变更
        req, _ = parse_tool_suite(raw)
        assert req.work_dir != seeded

    def test_force_keeps_legacy_dir_without_state(self, tmp_path, monkeypatch):
        """force + 旧版目录（无状态文件）→ 照续推（身份交 evalscope 侧校验）。"""
        monkeypatch.setattr(profiles, "common_path", lambda: tmp_path / "nope.yaml")
        seeded = self._seed(tmp_path, "20260929-120000")
        raw = self._raw(tmp_path, resume=True)
        req, _ = parse_tool_suite(raw)
        assert req.work_dir == seeded

    def test_off_always_fresh(self, tmp_path, monkeypatch):
        """resume: false → 即使上次未完成也全新（显式新任务）。"""
        monkeypatch.setattr(profiles, "common_path", lambda: tmp_path / "nope.yaml")
        seeded_raw = self._raw(tmp_path)
        seeded = self._seed(tmp_path, "20260929-120000", seeded_raw, status="running")
        raw = self._raw(tmp_path, resume=False)
        req, _ = parse_tool_suite(raw)
        assert req.resume is False and req.work_dir != seeded

    def test_normalize_resume_mode(self):
        assert normalize_resume_mode(None) == "auto"
        assert normalize_resume_mode("auto") == "auto"
        assert normalize_resume_mode(True) == "force"
        assert normalize_resume_mode("TRUE") == "force"
        assert normalize_resume_mode("force") == "force"
        assert normalize_resume_mode(False) == "off"
        assert normalize_resume_mode("false") == "off"
        assert normalize_resume_mode("off") == "off"
        with pytest.raises(ValueError, match="resume"):
            normalize_resume_mode("sometimes")

    def test_identity_ignores_judge_and_infra(self, tmp_path, monkeypatch):
        """判官/基础设施参数不在生成侧身份内；模型/limit/生成参数在。"""
        monkeypatch.setattr(profiles, "common_path", lambda: tmp_path / "nope.yaml")
        base_raw = self._raw(tmp_path)
        with_judge = self._raw(tmp_path, judge={"profile": "deepseek",
                                                "model": "deepseek-reasoner"})
        assert tool_identity_hash(base_raw) == tool_identity_hash(with_judge)
        assert tool_identity_hash(base_raw) != tool_identity_hash(
            self._raw(tmp_path, limit_per_subset=5))
        other_model = self._raw(tmp_path)
        other_model["target"] = {"base_url": "http://x/v1", "model": "other"}
        assert tool_identity_hash(base_raw) != tool_identity_hash(other_model)
        assert tool_identity_hash(base_raw) != tool_identity_hash(
            self._raw(tmp_path, extra_args={"generation_config": {"temperature": 0}}))
        infra = self._raw(tmp_path, extra_args={"timeout_s": 60, "python_exe": "x",
                                                "eval_batch_size": 2,
                                                "rerun_review": True})
        assert tool_identity_hash(base_raw) == tool_identity_hash(infra)


class TestResumeStateLifecycle:
    """run_tool_suite 落盘 run_state.json：running → completed / failed。"""

    def _fake_repo(self):
        class FakeRepo:
            async def upsert_suite(self, name, cfg):
                return "suite-1"

            async def create_run(self, name, cfg, suite_id):
                return "run-1"

            async def save_metric_summary(self, run_id, s):
                return None

            async def update_run(self, run_id, **kw):
                return None

        return FakeRepo()

    def _run(self, tmp_path, monkeypatch, boom=False):
        import asyncio

        from aite.tools.base import register_tool, unregister_tool

        @register_tool("fakebench_state")
        class FakeBench(ToolAdapter):
            def build_config(self, req):
                return {}

            async def run(self, req):
                if boom:
                    raise RuntimeError("boom")
                return ToolRunResult(
                    rows=[ToolMetricRow(dataset="evalscope:aa_lcr", metric="acc",
                                        value=0.5, count=10)],
                    raw_score={"aa_lcr": 0.5})

            def collect(self, req):
                return ToolRunResult()

            def list_tasks(self):
                return []

        try:
            raw = {"engine": "fakebench_state", "run_name": "aa_lcr",
                   "output_dir": str(tmp_path / "reports"),
                   "tools": {"datasets": ["aa_lcr"]}}
            monkeypatch.setattr(profiles, "common_path",
                                lambda: tmp_path / "nope.yaml")
            report = asyncio.run(run_tool_suite(raw, self._fake_repo()))
        finally:
            unregister_tool("fakebench_state")
        return report

    def _state_of(self, tmp_path):
        stamps = tmp_path / "reports" / "tool" / "fakebench_state" / "aa_lcr"
        stamp = next(iter(stamps.iterdir()))
        return json.loads((stamp / "run_state.json").read_text(encoding="utf-8")), stamp

    def test_success_marks_completed(self, tmp_path, monkeypatch):
        report = self._run(tmp_path, monkeypatch)
        state, _ = self._state_of(tmp_path)
        assert report.status == "passed"
        assert state["status"] == "completed" and state["identity_hash"]

    def test_failure_marks_failed_still_resumable(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch, boom=True)
        state, _ = self._state_of(tmp_path)
        assert state["status"] == "failed"

    def test_interrupted_run_is_resumed_by_auto(self, tmp_path, monkeypatch):
        """失败目录 + auto → 下次 parse 就地续推同一目录（断电/402 场景）。"""
        self._run(tmp_path, monkeypatch, boom=True)
        _, stamp = self._state_of(tmp_path)
        assert json.loads((stamp / "run_state.json").read_text(
            encoding="utf-8"))["status"] == "failed"
        raw = {"engine": "fakebench_state", "run_name": "aa_lcr",
               "output_dir": str(tmp_path / "reports"),
               "tools": {"datasets": ["aa_lcr"]}}
        # 身份按同一（隔离 common 的）解析口径计算，否则哈希对不上
        monkeypatch.setattr(profiles, "common_path", lambda: tmp_path / "nope.yaml")
        req, _ = parse_tool_suite(raw)
        assert req.work_dir == stamp


