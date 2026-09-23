"""工具适配层单测（全离线：不 import evalscope、不触网）。"""

import json
import pathlib

import pytest

from aite.tools import parse_tool_suite
from aite.tools.base import ToolRunRequest, get_tool
from aite.tools.evalscope_adapter import EvalscopeAdapter, _normalize_metric_names


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


class TestToolSuiteParsing:
    def test_parse_requires_datasets(self):
        with pytest.raises(ValueError, match="tools.datasets"):
            parse_tool_suite({"engine": "evalscope", "tools": {}})

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
