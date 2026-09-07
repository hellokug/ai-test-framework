"""evalscope-perf 适配器单测（build_config / collect fixture 解析 / 离线端到端）。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aite.tools.base import ToolRunRequest
from aite.tools.perf_adapter import PerfAdapter


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
