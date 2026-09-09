"""locust 适配器单测（build_config / 负载落盘 / CSV 解析 / 执行守卫）。"""

import json
from pathlib import Path

import pytest

from aite.tools.base import ToolRunRequest
from aite.tools.locust_adapter import LocustAdapter, _expand_ramp


def make_request(**kw):
    base = dict(
        run_name="perf",
        target={"type": "openai_compatible", "base_url": "http://localhost:11434/v1",
                "model": "qwen2.5:3b"},
        datasets=[],
        work_dir=Path("./outputs"),
        perf={"prompt": "写一段介绍", "parallel": [4], "duration": 60},
    )
    base.update(kw)
    return ToolRunRequest(**base)


class TestBuildConfig:
    def test_single_level_defaults(self):
        cfg = LocustAdapter().build_config(make_request(work_dir=Path("w")))
        assert cfg["url"] == "http://localhost:11434/v1/chat/completions"
        assert cfg["model"] == "qwen2.5:3b"
        assert cfg["stream"] is True and cfg["max_tokens"] is None
        run = cfg["runs"][0]
        assert run["users"] == 4 and run["seconds"] == 60.0
        assert run["spawn_rate"] == 4                       # 缺省 = 瞬时拉满
        assert run["csv"].endswith("locust_raw/locust_u4") or \
            run["csv"].replace("\\", "/").endswith("locust_raw/locust_u4")

    def test_mode_and_options_mapping(self):
        cfg = LocustAdapter().build_config(make_request(perf={
            "prompt": "p", "mode": "baseline", "baseline": {"parallel": 8},
            "level_duration": 120, "spawn_rate": 2, "stream": False,
            "max_tokens": 128}))
        run = cfg["runs"][0]
        assert run["users"] == 8 and run["seconds"] == 120.0
        assert run["spawn_rate"] == 2
        assert cfg["stream"] is False and cfg["max_tokens"] == 128

    def test_ramp_splits_total_duration(self):
        cfg = LocustAdapter().build_config(make_request(perf={
            "prompt": "p", "mode": "ramp",
            "ramp": {"from": 1, "to": 9, "step": 4}, "duration": 90}))
        assert _expand_ramp({"from": 1, "to": 9, "step": 4}) == [1, 5, 9]
        assert [(r["users"], r["seconds"]) for r in cfg["runs"]] == \
            [(1, 30.0), (5, 30.0), (9, 30.0)]               # 总时长均分 3 档

    def test_v1_direct_gradient(self):
        cfg = LocustAdapter().build_config(make_request(
            perf={"prompt": "p", "parallel": [1, 4], "level_duration": 30}))
        assert [(r["users"], r["seconds"]) for r in cfg["runs"]] == \
            [(1, 30.0), (4, 30.0)]

    def test_validation_errors(self):
        cases = [
            ({"prompt": "p", "parallel": [4]}, "时长"),               # 缺时长
            ({"prompt": "p", "duration": 60, "level_duration": 30}, "二选一"),
            ({"prompt": "p", "duration": -1}, "duration"),
            ({"prompt": "p", "level_duration": 0}, "level_duration"),
            ({"prompt": "p", "mode": "probe", "probe": {"max_parallel": 8,
                                                        "sla": {"avg_ttft": 1}},
              "duration": 60}, "probe"),
            ({"prompt": "p", "mode": "baseline", "baseline": {}}, "baseline"),
            ({"prompt": "p", "mode": "ramp", "ramp": {"from": 9, "to": 1}}, "ramp"),
            ({"prompt": "p", "mode": "baseline", "baseline": {"parallel": 4},
              "parallel": [1], "duration": 60}, "不要直接配"),
            ({"prompt": "p", "parallel": [0], "duration": 60}, "parallel"),
            ({"dataset": "a.jsonl", "prompt": "p", "duration": 60}, "二选一"),
            ({}, "perf.dataset"),
        ]
        for perf, err in cases:
            with pytest.raises(ValueError, match=err):
                LocustAdapter().build_config(make_request(perf=perf))

    def test_number_ignored_not_error(self):
        cfg = LocustAdapter().build_config(make_request(
            perf={"prompt": "p", "parallel": [4], "duration": 60, "number": 8}))
        assert cfg["runs"][0]["users"] == 4                  # 忽略不报错

    def test_missing_model_raises(self):
        with pytest.raises(ValueError, match="target.model"):
            LocustAdapter().build_config(make_request(
                target={"type": "openai_compatible", "base_url": "http://x/v1"}))

    def test_list_tasks_shape(self):
        tasks = LocustAdapter().list_tasks()
        assert tasks[0]["name"] == "chat"
        assert "ttft_ms" in tasks[0]["metrics"]
        assert "tpot_ms" not in tasks[0]["metrics"]          # locust 无来源


class TestWriteLoad:
    def test_prompt_mode(self, tmp_path):
        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path,
                                                        perf={"prompt": "唯一提问",
                                                              "parallel": [2],
                                                              "duration": 10}))
        LocustAdapter()._write_load(cfg)
        assert (tmp_path / "locustfile.py").is_file()
        assert (tmp_path / "load" / "prompts.txt").read_text(
            encoding="utf-8").splitlines() == ["唯一提问"]

    def test_dataset_mode(self, tmp_path):
        ds = tmp_path / "ds.jsonl"
        ds.write_text(json.dumps({"question": "甲"}) + "\n"
                      + json.dumps({"input": "乙"}) + "\n", encoding="utf-8")
        cfg = LocustAdapter().build_config(make_request(
            work_dir=tmp_path,
            perf={"dataset": str(ds), "parallel": [2], "duration": 10}))
        LocustAdapter()._write_load(cfg)
        assert (tmp_path / "load" / "prompts.txt").read_text(
            encoding="utf-8").splitlines() == ["甲", "乙"]

    def test_locustfile_reads_env(self, tmp_path):
        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path))
        LocustAdapter()._write_load(cfg)
        text = (tmp_path / "locustfile.py").read_text(encoding="utf-8")
        for key in ("AITE_LOCUST_URL", "AITE_LOCUST_MODEL", "AITE_LOCUST_STREAM",
                    "AITE_LOCUST_PROMPTS_FILE"):
            assert key in text


def _write_stats(directory: Path, users: int, agg: dict, ttft: dict | None) -> None:
    directory.mkdir(parents=True)
    cols = ["Type", "Name", "Request Count", "Failure Count", "Median Response Time",
            "Average Response Time", "Min Response Time", "Max Response Time",
            "Average Content Size", "Requests/s", "Failures/s",
            "50%", "66%", "75%", "80%", "90%", "95%", "98%", "99%", "99.9%",
            "99.99%", "100%"]
    post = {"Type": "POST", "Name": "POST /chat/completions",
            "Request Count": str(agg["Request Count"]),
            "Failure Count": str(agg["Failure Count"]),
            "Average Response Time": agg["Average Response Time"],
            "Requests/s": agg["Requests/s"], "95%": agg["95%"]}
    rows = [post]
    if ttft is not None:
        rows.append({"Type": "TTFT", "Name": "POST /chat/completions",
                     "Request Count": agg["Request Count"], "Failure Count": "0",
                     "Average Response Time": ttft[0], "95%": ttft[1]})
    rows.append(agg)
    import csv as _csv
    with (directory / f"locust_u{users}_stats.csv").open("w", newline="",
                                                         encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


class TestCollect:
    def test_metrics_rows_with_stream_ttft(self, tmp_path):
        agg = {"Type": "Aggregated", "Name": "Aggregated", "Request Count": "100",
               "Failure Count": "3", "Average Response Time": "9100",
               "Requests/s": "0.55", "95%": "14550"}
        _write_stats(tmp_path / "locust_raw", 4, agg, ("560", "1100"))
        (tmp_path / "locust_raw" / "locust_u4_failures.csv").write_text(
            "Method,Name,Error,Occurrences\n"
            "POST,POST /chat/completions,\"HTTPError('HTTP 429')\",3\n",
            encoding="utf-8")
        result = LocustAdapter().collect(make_request(
            work_dir=tmp_path, metric_thresholds={"error_rate": 0.01,
                                                  "qps": 0.1}))
        by_metric = {r.metric: r for r in result.rows}
        assert all(r.dataset == "locust:u4" and r.count == 100 for r in result.rows)
        assert by_metric["latency_avg_ms"].value == pytest.approx(9100)
        assert by_metric["latency_p95_ms"].value == pytest.approx(14550)
        assert by_metric["ttft_ms"].value == pytest.approx(560)
        assert by_metric["ttft_p95_ms"].value == pytest.approx(1100)
        assert by_metric["qps"].value == pytest.approx(0.55)
        assert by_metric["error_rate"].value == pytest.approx(0.03)
        assert by_metric["error_rate"].passed is False        # 0.03 > 0.01（越低越好）
        assert by_metric["qps"].passed is True                # 0.55 >= 0.1（越高越好）
        assert by_metric["error_rate"].higher_is_better is False
        assert by_metric["qps"].higher_is_better is True
        assert result.raw_score == {"locust:u4": 0.55}
        assert result.failures == [{"dataset": "locust:u4",
                                    "case": "POST",
                                    "detail": "HTTPError('HTTP 429') ×3"}]

    def test_non_stream_no_ttft_rows(self, tmp_path):
        agg = {"Type": "Aggregated", "Name": "Aggregated", "Request Count": "10",
               "Failure Count": "0", "Average Response Time": "900",
               "Requests/s": "1.5", "95%": "1200"}
        _write_stats(tmp_path / "locust_raw", 2, agg, None)
        result = LocustAdapter().collect(make_request(work_dir=tmp_path))
        names = {r.metric for r in result.rows}
        assert "ttft_ms" not in names and "latency_avg_ms" in names

    def test_no_output_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="压测产出"):
            LocustAdapter().collect(make_request(work_dir=tmp_path))

    def test_missing_aggregated_row_skipped(self, tmp_path):
        (tmp_path / "locust_raw").mkdir(parents=True)
        (tmp_path / "locust_raw" / "locust_u4_stats.csv").write_text(
            "Type,Name\nPOST,x\n", encoding="utf-8")
        result = LocustAdapter().collect(make_request(work_dir=tmp_path))
        assert result.rows == []                              # 警告跳过，不抛错


class TestExec:
    def test_missing_binary_raises(self, tmp_path, monkeypatch):
        import shutil as sh
        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path))
        LocustAdapter()._write_load(cfg)
        monkeypatch.setattr(sh, "which", lambda name: None)
        with pytest.raises(RuntimeError, match="locust"):
            LocustAdapter()._exec(cfg)
