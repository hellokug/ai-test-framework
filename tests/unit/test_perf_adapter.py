"""evalscope-perf 适配器单测（build_config / collect fixture 解析 / 离线端到端）。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aite.tools.base import ToolRunRequest
from aite.tools.perf_adapter import (
    SOAK_NUMBER_CAP,
    PerfAdapter,
    _expand_ramp,
    _export_prompts,
    _sla_params_json,
)


def make_request(**kw):
    base = dict(
        run_name="perf",
        target={"type": "openai_compatible", "base_url": "http://localhost:11434/v1",
                "model": "qwen2.5:3b"},
        datasets=[],
        work_dir=Path("./outputs"),
        perf={"prompt": "写一段介绍", "parallel": [1, 4], "number": 8},
    )
    base.update(kw)
    return ToolRunRequest(**base)


class TestBuildConfig:
    def test_args_mapping(self):
        cfg = PerfAdapter().build_config(make_request(work_dir=Path("w")))
        args = cfg["args"]
        assert args[0] == "perf"
        assert "--parallel" in args
        i = args.index("--parallel")
        assert args[i + 1:i + 3] == ["1", "4"]
        j = args.index("--number")
        assert args[j + 1:j + 3] == ["8", "8"]      # 标量自动对齐梯度长度
        assert args[args.index("--url") + 1] == "http://localhost:11434/v1/chat/completions"
        assert args[args.index("--model") + 1] == "qwen2.5:3b"
        assert args[args.index("--prompt") + 1] == "写一段介绍"
        assert "--stream" in args and "--no-stream" not in args   # 默认流式（真 TTFT）
        assert args[args.index("--outputs-dir") + 1] == str(Path("w/perf_raw"))
        assert cfg["timeout"] == 3600

    def test_explicit_options(self):
        cfg = PerfAdapter().build_config(make_request(perf={
            "prompt": "p", "parallel": [2], "number": [5],
            "max_tokens": 64, "warmup_num": 3, "stream": False,
            "no_test_connection": True}))
        args = cfg["args"]
        assert args[args.index("--number") + 1] == "5"
        assert args[args.index("--max-tokens") + 1] == "64"
        assert args[args.index("--warmup-num") + 1] == "3"
        assert "--no-stream" in args and "--stream" not in args
        assert "--no-test-connection" in args

    def test_timeout_from_extra(self):
        cfg = PerfAdapter().build_config(make_request(extra={"timeout": 60}))
        assert cfg["timeout"] == 60

    @pytest.mark.parametrize("perf,err_match", [
        ({"parallel": [1]}, "prompt"),                                   # 缺 prompt
        ({"prompt": "p", "parallel": [0]}, "parallel"),                  # 并发 <1
        ({"prompt": "p", "parallel": ["4"]}, "parallel"),                # 非整数
        ({"prompt": "p", "parallel": [1], "number": [1, 2]}, "number"),  # 长度不齐
        ({"prompt": "p", "parallel": [1], "number": 0}, "number"),       # 采样 <1
    ])
    def test_validation_errors(self, perf, err_match):
        with pytest.raises(ValueError, match=err_match):
            PerfAdapter().build_config(make_request(perf=perf))

    def test_missing_model_raises(self):
        with pytest.raises(ValueError, match="target.model"):
            PerfAdapter().build_config(make_request(
                target={"type": "openai_compatible", "base_url": "http://x/v1"}))

    def test_list_tasks_shape(self):
        tasks = PerfAdapter().list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["name"] == "chat" and "ttft_ms" in tasks[0]["metrics"]


class TestModes:
    """模式层：baseline / ramp / probe / soak → CLI 映射。"""

    def test_baseline(self):
        cfg = PerfAdapter().build_config(make_request(perf={
            "prompt": "p", "mode": "baseline", "baseline": {"parallel": 4}}))
        args = cfg["args"]
        i = args.index("--parallel")
        assert args[i + 1] == "4"
        j = args.index("--number")
        assert len(args) == j + 2 or args[j + 2].startswith("--")   # number 单值

    def test_ramp_expands_with_to_included(self):
        assert _expand_ramp({"from": 1, "to": 16, "step": 4}) == [1, 5, 9, 13, 16]
        assert _expand_ramp({"from": 1, "to": 8, "step": 4}) == [1, 5, 8]
        assert _expand_ramp({"from": 2, "to": 2, "step": 3}) == [2]
        cfg = PerfAdapter().build_config(make_request(perf={
            "prompt": "p", "mode": "ramp",
            "ramp": {"from": 1, "to": 9, "step": 4}, "duration": 30, "number": 5}))
        args = cfg["args"]
        i = args.index("--parallel")
        assert args[i + 1:i + 4] == ["1", "5", "9"]        # to=9 已含，3 档
        assert args[args.index("--duration") + 1] == "30"  # 单档运行秒数
        j = args.index("--number")
        assert args[j + 1:j + 4] == ["5", "5", "5"]        # 标量对齐档数

    def test_probe_sla_params(self):
        assert json.loads(_sla_params_json({"avg_ttft": 2000, "p99_latency": 10})) == \
            [{"avg_ttft": "< 2000", "p99_latency": "< 10"}]
        cfg = PerfAdapter().build_config(make_request(perf={
            "prompt": "p", "mode": "probe",
            "probe": {"max_parallel": 32, "from": 2, "sla": {"avg_ttft": 2000},
                      "min_parallel": 1, "runs": 3}}))
        args = cfg["args"]
        assert "--sla-auto-tune" in args
        assert args[args.index("--sla-variable") + 1] == "parallel"
        assert args[args.index("--sla-upper-bound") + 1] == "32"
        assert json.loads(args[args.index("--sla-params") + 1]) == [{"avg_ttft": "< 2000"}]
        assert args[args.index("--sla-num-runs") + 1] == "3"
        i = args.index("--parallel")
        assert args[i + 1] == "2"                          # 搜索起点

    def test_soak_duration_and_number_cap(self):
        cfg = PerfAdapter().build_config(make_request(perf={
            "prompt": "p", "mode": "soak", "soak": {"parallel": 8}, "duration": 3600}))
        args = cfg["args"]
        assert args[args.index("--parallel") + 1] == "8"
        assert args[args.index("--duration") + 1] == "3600"
        assert args[args.index("--number") + 1] == str(SOAK_NUMBER_CAP)  # 安全上限

    def test_mode_conflicts_and_errors(self):
        with pytest.raises(ValueError, match="不要直接配"):
            PerfAdapter().build_config(make_request(perf={
                "prompt": "p", "mode": "baseline", "baseline": {"parallel": 4},
                "parallel": [1]}))
        for perf, err in [
            ({"prompt": "p", "mode": "wizard"}, "mode"),
            ({"prompt": "p", "mode": "baseline", "baseline": {}}, "baseline"),
            ({"prompt": "p", "mode": "ramp", "ramp": {"from": 9, "to": 1, "step": 2}}, "ramp"),
            ({"prompt": "p", "mode": "soak", "soak": {}, "duration": 60}, "soak"),
            ({"prompt": "p", "mode": "probe", "probe": {"sla": {"avg_ttft": 1}}}, "max_parallel"),
            ({"prompt": "p", "mode": "probe", "probe": {"max_parallel": 8,
                                                        "sla": {"nope": 1}}}, "sla"),
            ({"prompt": "p", "duration": -1}, "duration"),
        ]:
            with pytest.raises(ValueError, match=err):
                PerfAdapter().build_config(make_request(perf=perf))

    def test_v1_compat_without_mode(self):
        """省略 mode = v1 直配 parallel/number，行为不变。"""
        cfg = PerfAdapter().build_config(make_request(
            perf={"prompt": "p", "parallel": [1, 4], "number": 8}))
        i = cfg["args"].index("--parallel")
        assert cfg["args"][i + 1:i + 3] == ["1", "4"]


class TestDatasetLoad:
    """负载层：数据集导出 + max_tokens 区间。"""

    def test_export_jsonl(self, tmp_path):
        src = tmp_path / "ds.jsonl"
        src.write_text("\n".join([
            json.dumps({"id": 1, "question": "第一个问题"}),
            "",
            json.dumps({"id": 2, "query": "第二个问题"}),
            json.dumps({"id": 3, "input": "第三个问题"}),
        ]) + "\n", encoding="utf-8")
        dest = tmp_path / "load" / "prompts.txt"
        assert _export_prompts(str(src), dest) == 3
        assert dest.read_text(encoding="utf-8").splitlines() == \
            ["第一个问题", "第二个问题", "第三个问题"]

    def test_export_csv(self, tmp_path):
        src = tmp_path / "ds.csv"
        src.write_text("id,prompt\n1,甲\n2,乙\n", encoding="utf-8")
        dest = tmp_path / "load" / "prompts.txt"
        assert _export_prompts(str(src), dest) == 2

    def test_export_errors(self, tmp_path):
        with pytest.raises(ValueError, match="不存在"):
            _export_prompts(str(tmp_path / "nope.jsonl"), tmp_path / "d.txt")
        bad = tmp_path / "bad.jsonl"
        bad.write_text(json.dumps({"id": 1, "answer": "无输入字段"}) + "\n",
                       encoding="utf-8")
        with pytest.raises(ValueError, match="无输入字段"):
            _export_prompts(str(bad), tmp_path / "d.txt")

    def test_build_config_dataset_args(self):
        cfg = PerfAdapter().build_config(make_request(work_dir=Path("w"), perf={
            "dataset": "ds.jsonl", "mode": "baseline", "baseline": {"parallel": 2},
            "max_tokens": [64, 256]}))
        args = cfg["args"]
        assert args[args.index("--dataset") + 1] == "custom"
        assert args[args.index("--dataset-path") + 1] == str(Path("w/load/prompts.txt"))
        mt = args.index("--max-tokens")
        assert args[mt + 1:mt + 3] == ["64", "256"]         # 区间采样
        assert cfg["dataset"] == "ds.jsonl"
        assert "--prompt" not in args

    def test_dataset_and_prompt_mutually_exclusive(self):
        with pytest.raises(ValueError, match="二选一"):
            PerfAdapter().build_config(make_request(
                perf={"dataset": "a.jsonl", "prompt": "p"}))
        with pytest.raises(ValueError, match="perf.dataset"):
            PerfAdapter().build_config(make_request(perf={}))

    def test_max_tokens_bad_range(self):
        with pytest.raises(ValueError, match="max_tokens"):
            PerfAdapter().build_config(make_request(
                perf={"prompt": "p", "max_tokens": [100, 10]}))
        with pytest.raises(ValueError, match="max_tokens"):
            PerfAdapter().build_config(make_request(
                perf={"prompt": "p", "max_tokens": [1, 2, 3]}))


SUMMARY = {
    "Test Duration (s)": 14.55, "Concurrency": 4, "Total Requests": 8,
    "Success Requests": 8, "Failed Requests": 0, "Req Throughput (req/s)": 0.55,
    "Avg Latency (s)": 9.10, "Output Throughput (tok/s)": 8.8,
    "TTFT (ms)": 5591.59, "TPOT (ms)": 113.3, "ITL (ms)": 109.76,
}
PERCENTILE = [
    {"Percentiles": "50%", "Latency (s)": 10.92, "TTFT (ms)": 7405.51},
    {"Percentiles": "95%", "Latency (s)": 14.55, "TTFT (ms)": 11042.56},
]


class TestCollect:
    def _write_level(self, tmp_path, level_dir, summary, percentile=None):
        d = tmp_path / "perf_raw" / "m" / level_dir
        d.mkdir(parents=True)
        (d / "benchmark_summary.json").write_text(
            json.dumps(summary), encoding="utf-8")
        if percentile is not None:
            (d / "benchmark_percentile.json").write_text(
                json.dumps(percentile), encoding="utf-8")

    def test_metrics_rows_per_level(self, tmp_path):
        self._write_level(tmp_path, "parallel_4_number_8", SUMMARY, PERCENTILE)
        result = PerfAdapter().collect(make_request(
            work_dir=tmp_path,
            metric_thresholds={"ttft_ms": 5000}))
        by_metric = {r.metric: r for r in result.rows}
        assert all(r.dataset == "perf:c4" and r.count == 8 for r in result.rows)
        assert by_metric["ttft_ms"].value == pytest.approx(5591.59)
        assert by_metric["ttft_ms"].threshold == 5000
        assert by_metric["ttft_ms"].passed is False   # 5591.59 > 5000（越低越好）
        assert by_metric["latency_avg_ms"].value == pytest.approx(9100)
        assert by_metric["latency_p95_ms"].value == pytest.approx(14550)  # 秒→毫秒
        assert by_metric["ttft_p95_ms"].value == pytest.approx(11042.56)
        assert by_metric["qps"].value == pytest.approx(0.55)
        assert by_metric["output_tps"].value == pytest.approx(8.8)
        assert by_metric["error_rate"].value == 0.0
        assert by_metric["tpot_ms"].passed is None        # 未配阈值无判定
        assert result.raw_score == {"perf:c4": 8.8}

    def test_error_rate_computed(self, tmp_path):
        bad = dict(SUMMARY, **{"Failed Requests": 2, "Success Requests": 6})
        self._write_level(tmp_path, "parallel_1_number_8", bad)
        result = PerfAdapter().collect(make_request(work_dir=tmp_path))
        rate = {r.metric: r for r in result.rows}["error_rate"]
        assert rate.value == pytest.approx(0.25)

    def test_threshold_directions(self, tmp_path):
        """阈值方向：延迟/错误率越低越好（<=）；qps/output_tps 越高越好（>=）。"""
        self._write_level(tmp_path, "parallel_1_number_8", SUMMARY, PERCENTILE)
        result = PerfAdapter().collect(make_request(work_dir=tmp_path, metric_thresholds={
            "error_rate": 0.05,     # 0.0 <= 0.05 → PASS
            "ttft_ms": 6000,        # 5591.59 <= 6000 → PASS
            "latency_p95_ms": 1000,  # 14550 > 1000 → FAIL
            "qps": 1.0,             # 0.55 < 1.0 → FAIL（越高越好）
            "output_tps": 5.0,      # 8.8 >= 5.0 → PASS
        }))
        by_metric = {r.metric: r for r in result.rows}
        assert by_metric["error_rate"].passed is True
        assert by_metric["ttft_ms"].passed is True
        assert by_metric["latency_p95_ms"].passed is False
        assert by_metric["qps"].passed is False
        assert by_metric["output_tps"].passed is True
        assert by_metric["ttft_ms"].higher_is_better is False
        assert by_metric["qps"].higher_is_better is True

    def test_missing_percentile_tolerated(self, tmp_path):
        self._write_level(tmp_path, "parallel_1_number_8", SUMMARY)   # 无分位文件
        result = PerfAdapter().collect(make_request(work_dir=tmp_path))
        names = {r.metric for r in result.rows}
        assert "ttft_p95_ms" not in names and "ttft_ms" in names

    def test_no_output_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="压测产出"):
            PerfAdapter().collect(make_request(work_dir=tmp_path))


class _FakePerfHandler(BaseHTTPRequestHandler):
    """最小 OpenAI 兼容端点（含 usage），供 evalscope perf 离线端到端。"""

    def do_POST(self):
        body = json.dumps({
            "choices": [{"message": {"content": "hello world"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def test_run_offline_end_to_end(tmp_path):
    """fake 端点跑通 evalscope perf 子进程全链路：执行→产出→collect 指标行。"""
    import shutil

    if shutil.which("evalscope") is None:      # pragma: no cover - 环境守卫
        pytest.skip("evalscope CLI 不可用")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakePerfHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = make_request(
            work_dir=tmp_path,
            target={"type": "openai_compatible",
                    "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
                    "model": "fake-model"},
            perf={"prompt": "hi", "parallel": [1], "number": 2,
                  "max_tokens": 8, "stream": False, "no_test_connection": True},
            metric_thresholds={"qps": 0.0})
        result = await PerfAdapter().run(req)
    finally:
        server.shutdown()
    rows = {(r.dataset, r.metric): r for r in result.rows}
    assert ("perf:c1", "qps") in rows
    assert rows[("perf:c1", "qps")].passed is True   # 阈值 0 恒过，链路判定可用
    assert (tmp_path / "logs" / "perf.log").is_file()


async def test_run_dataset_load_end_to_end(tmp_path):
    """数据集负载端到端：jsonl 导出 → evalscope custom 数据集逐条取用。"""
    import shutil

    if shutil.which("evalscope") is None:      # pragma: no cover - 环境守卫
        pytest.skip("evalscope CLI 不可用")
    ds = tmp_path / "ds.jsonl"
    ds.write_text("\n".join(
        json.dumps({"id": i, "question": f"问题 {i}"}) for i in range(1, 4)) + "\n",
        encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakePerfHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = make_request(
            work_dir=tmp_path,
            target={"type": "openai_compatible",
                    "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
                    "model": "fake-model"},
            perf={"dataset": str(ds), "mode": "baseline",
                  "baseline": {"parallel": 2}, "number": 3, "stream": False,
                  "no_test_connection": True},
            metric_thresholds={"error_rate": 0.0})
        result = await PerfAdapter().run(req)
    finally:
        server.shutdown()
    exported = (tmp_path / "load" / "prompts.txt").read_text(encoding="utf-8")
    assert exported.splitlines() == ["问题 1", "问题 2", "问题 3"]   # 导出落盘
    rows = {(r.dataset, r.metric): r for r in result.rows}
    assert rows[("perf:c2", "error_rate")].passed is True


async def test_run_probe_mode_end_to_end(tmp_path):
    """摸高模式端到端：SLA 自动搜索产出 sla_summary + 各档指标行。"""
    import shutil

    if shutil.which("evalscope") is None:      # pragma: no cover - 环境守卫
        pytest.skip("evalscope CLI 不可用")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakePerfHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = make_request(
            work_dir=tmp_path,
            target={"type": "openai_compatible",
                    "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
                    "model": "fake-model"},
            perf={"prompt": "hi", "mode": "probe",
                  "probe": {"max_parallel": 4, "sla": {"avg_ttft": 60000},
                            "runs": 1},
                  "stream": False, "no_test_connection": True})
        result = await PerfAdapter().run(req)
    finally:
        server.shutdown()
    assert result.rows, "摸高模式应产出指标行（各搜索档）"
    assert any("sla_summary.json" in name for name in result.report_files)
