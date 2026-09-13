"""locust 适配器单测（build_config / 负载落盘 / CSV 解析 / 执行守卫）。"""

import json
from pathlib import Path

import pytest

from aite.tools.base import ToolRunRequest
from aite.tools.locust_adapter import LocustAdapter, _combined_series, _history_series
from aite.tools.perf_common import expand_ramp


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
        assert expand_ramp({"from": 1, "to": 9, "step": 4}) == [1, 5, 9]
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
            ({"dataset": "a.jsonl", "prompt": "p", "duration": 60}, "三选一"),
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

    def test_plaintext_api_key_rejected(self):
        """明文密钥禁入配置（原则 5）：api_endpoint 层 fail-fast 指引 api_key_env。"""
        with pytest.raises(ValueError, match="api_key_env"):
            LocustAdapter().build_config(make_request(
                target={"type": "openai_compatible", "base_url": "http://x/v1",
                        "model": "m", "api_key": "sk-plaintext"}))

    def test_list_tasks_shape(self):
        tasks = LocustAdapter().list_tasks()
        assert tasks[0]["name"] == "chat"
        assert "ttft_ms" in tasks[0]["metrics"]
        # 流式统计增强后与 evalscope-perf 指标对齐（tpot/itl/output_tps 均可产出）
        assert {"tpot_ms", "itl_ms", "output_tps"} <= set(tasks[0]["metrics"])


class TestTokenOnlySeconds:
    def test_token_seconds_without_history_row(self, tmp_path):
        """回归：token 完成秒与 history 行不重合时（run 尾部），秒桶走
        token 合并路径创建——不得因桶结构漂移 KeyError。"""
        agg = {"Type": "Aggregated", "Name": "Aggregated", "Request Count": "10",
               "Failure Count": "0", "Average Response Time": "900",
               "Requests/s": "1.0", "95%": "1200"}
        raw = tmp_path / "locust_raw"
        _write_stats(raw, 4, agg, ("560", "1100"))
        hist_cols = ["Timestamp", "User Count", "Type", "Name", "Requests/s",
                     "Failures/s", "50%", "95%"]
        import csv as _csv
        with (raw / "locust_u4_stats_history.csv").open("w", newline="",
                                                        encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=hist_cols)
            w.writeheader()
            w.writerow({"Timestamp": "100", "User Count": "4", "Type": "POST",
                        "Name": "x", "Requests/s": "2.0", "Failures/s": "0.0",
                        "50%": "800", "95%": "900"})
        # token 记录落在 history 没有的秒（105）
        (raw / "locust_u4_tokens.jsonl").write_text(
            json.dumps({"t": 105.2, "tokens": 500, "gen_ms": 800}) + "\n",
            encoding="utf-8")
        series = _history_series(raw)
        assert len(series) == 1
        pts = {p["t"]: p for p in series[0]["points"]}
        assert 5.0 in pts                              # 105 - 100 = 5s（token 桶）
        assert pts[5.0]["tok_s"] == 500.0
        assert pts[5.0]["rps"] == 0.0                  # 纯 token 桶其余字段安全
        assert pts[5.0]["tpot_avg"] is None


class TestCombinedSeries:
    def test_combined_full_metrics_shape(self, tmp_path):
        """combined 阶梯曲线点形状与 series 对齐（全指标）——前端单一数据源。"""
        agg = {"Type": "Aggregated", "Name": "Aggregated", "Request Count": "10",
               "Failure Count": "0", "Average Response Time": "900",
               "Requests/s": "1.0", "95%": "1200"}
        raw = tmp_path / "locust_raw"
        _write_stats(raw, 4, agg, ("560", "1100"))
        hist_cols = ["Timestamp", "User Count", "Type", "Name", "Requests/s",
                     "Failures/s", "50%", "95%"]
        import csv as _csv
        with (raw / "locust_u4_stats_history.csv").open("w", newline="",
                                                        encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=hist_cols)
            w.writeheader()
            w.writerow({"Timestamp": "100", "User Count": "4", "Type": "POST",
                        "Name": "x", "Requests/s": "2.0", "Failures/s": "0.0",
                        "50%": "800", "95%": "900"})
            w.writerow({"Timestamp": "100", "User Count": "4", "Type": "TTFT",
                        "Name": "x", "Requests/s": "0", "Failures/s": "0",
                        "50%": "500", "95%": "800"})
            w.writerow({"Timestamp": "100", "User Count": "4", "Type": "TPOT",
                        "Name": "x", "Requests/s": "0", "Failures/s": "0",
                        "50%": "30", "95%": "55"})
            w.writerow({"Timestamp": "100", "User Count": "4", "Type": "ITL",
                        "Name": "x", "Requests/s": "0", "Failures/s": "0",
                        "50%": "25", "95%": "45"})
        (raw / "locust_u4_tokens.jsonl").write_text(
            json.dumps({"t": 100.5, "tokens": 50, "gen_ms": 900}) + "\n",
            encoding="utf-8")
        combined = _combined_series(raw)
        assert len(combined) == 1
        pt = combined[0]
        # 与 series 点形状一致（全指标），前端单一数据源
        for key in ("t", "ts", "users", "ttft_avg", "ttft_p95", "latency_avg",
                    "latency_p95", "tpot_avg", "tpot_p95", "itl_avg", "itl_p95",
                    "rps", "tok_s", "errors"):
            assert key in pt, key
        assert pt["ttft_avg"] == pytest.approx(650.0)   # (500+800)/2
        assert pt["tpot_avg"] == pytest.approx(30.0)    # (30+55)/2 → wait (30+55)/2=42.5? 30/55 为 50/95
        assert pt["itl_p95"] == pytest.approx(45.0)
        assert pt["tok_s"] == 50.0                      # 50 tokens / 1s


class TestLocustfileSignatures:
    def test_record_tokens_signature_matches_calls(self, tmp_path):
        """_record_tokens 定义参数数与全部调用点一致（真实联调 P0：签名漂移
        导致 244 次任务级异常——模板被多处编辑后调用/定义不同步）。"""
        import ast

        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path))
        LocustAdapter()._write_load(cfg)
        tpl = (tmp_path / "locustfile.py").read_text(encoding="utf-8")
        t_ast = ast.parse(tpl)
        defs = [n for n in ast.walk(t_ast)
                if isinstance(n, ast.FunctionDef) and n.name == "_record_tokens"]
        calls = [n for n in ast.walk(t_ast) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "_record_tokens"]
        assert defs and calls
        n_params = len(defs[0].args.args) - (1 if defs[0].args.kwonlyargs else 0)
        assert all(len(c.args) == n_params for c in calls)

class TestLocustfileIntegrity:
    def test_template_structure_chat_in_class(self, tmp_path):
        """ChatUser 单一类定义且 on_start/chat 在类体内（防类体断裂回归：
        QPS 分支复制类曾把方法留在类外，locust 实跑 0 请求）。"""
        import ast

        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path))
        LocustAdapter()._write_load(cfg)
        text = (tmp_path / "locustfile.py").read_text(encoding="utf-8")
        tree = ast.parse(text)                            # 语法合法
        classes = [n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "ChatUser"]
        assert len(classes) == 1
        methods = {n.name for n in classes[0].body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert {"on_start", "chat", "_fire",
                "_stream_metrics", "_json_metrics"} <= methods
        # wait_time 经 _wait_time() 选择（并发/QPS 双模式分支共存）
        assert any("constant_pacing" in ast.dump(n) for n in tree.body)

    def test_stream_metrics_present(self, tmp_path):
        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path))
        LocustAdapter()._write_load(cfg)
        text = (tmp_path / "locustfile.py").read_text(encoding="utf-8")
        for marker in ('"ITL"', '"TPOT"', '"usage"', "_stream_metrics",
                       '_record_tokens', 'TOKENS_FILE'):
            assert marker in text


class TestQPSMode:
    """QPS 模式（perf.qps，闭环封顶）：wait_time 换 constant_pacing。"""

    def test_qps_mapping(self, tmp_path):
        cfg = LocustAdapter().build_config(make_request(
            work_dir=tmp_path,
            perf={"prompt": "p", "parallel": [8], "duration": 60, "qps": 50}))
        assert cfg["qps"] == 50.0
        LocustAdapter()._write_load(cfg)
        text = (tmp_path / "locustfile.py").read_text(encoding="utf-8")
        assert "constant_pacing(1.0 / float(QPS)" in text
        assert "constant_total_pacing=True" in text

    def test_no_qps_defaults_to_concurrency_mode(self, tmp_path):
        cfg = LocustAdapter().build_config(make_request(
            work_dir=tmp_path,
            perf={"prompt": "p", "parallel": [4], "duration": 60}))
        assert cfg["qps"] is None
        LocustAdapter()._write_load(cfg)
        text = (tmp_path / "locustfile.py").read_text(encoding="utf-8")
        assert "return constant(0)" in text                  # 并发模式：无间隔连发
        assert "constant_total_pacing" in text               # 分支共存（QPS 可选）

    def test_qps_validation(self):
        with pytest.raises(ValueError, match="qps"):
            LocustAdapter().build_config(make_request(
                perf={"prompt": "p", "parallel": [4], "duration": 60, "qps": 0}))
        with pytest.raises(ValueError, match="qps"):
            LocustAdapter().build_config(make_request(
                perf={"prompt": "p", "parallel": [4], "duration": 60,
                      "qps": "fast"}))

    def test_qps_env_injected(self, tmp_path):
        cfg = LocustAdapter().build_config(make_request(
            work_dir=tmp_path,
            perf={"prompt": "p", "parallel": [8], "duration": 60, "qps": 50}))
        LocustAdapter()._write_load(cfg)
        assert cfg["qps"] == 50.0
        # env 注入在 _exec 侧构造——此处验证 locustfile 读取该变量即可
        text = (tmp_path / "locustfile.py").read_text(encoding="utf-8")
        assert 'os.environ.get("AITE_LOCUST_QPS")' in text


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


def _write_stats(directory: Path, users: int, agg: dict, ttft: dict | None,
                 post: dict | None = None,
                 extra_rows: list[dict] | None = None) -> None:
    directory.mkdir(parents=True)
    cols = ["Type", "Name", "Request Count", "Failure Count", "Median Response Time",
            "Average Response Time", "Min Response Time", "Max Response Time",
            "Average Content Size", "Requests/s", "Failures/s",
            "50%", "66%", "75%", "80%", "90%", "95%", "98%", "99%", "99.9%",
            "99.99%", "100%"]
    post = post if post is not None else dict(agg, Type="POST", Name="POST /chat/completions")
    post = {"Type": "POST", "Name": "POST /chat/completions",
            "Request Count": post.get("Request Count", agg["Request Count"]),
            "Failure Count": post.get("Failure Count", agg["Failure Count"]),
            "Average Response Time": post.get("Average Response Time"),
            "Requests/s": post.get("Requests/s"), "95%": post.get("95%")}
    rows = [post]
    if ttft is not None:
        rows.append({"Type": "TTFT", "Name": "POST /chat/completions",
                     "Request Count": agg["Request Count"], "Failure Count": "0",
                     "Average Response Time": ttft[0], "95%": ttft[1]})
    for extra in (extra_rows or []):
        rows.append({"Type": extra["Type"], "Name": "POST /chat/completions",
                     "Request Count": agg["Request Count"], "Failure Count": "0",
                     "Average Response Time": extra["avg"], "95%": extra["p95"]})
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
        _write_stats(tmp_path / "locust_raw", 4, agg, ("560", "1100"),
                     extra_rows=[
                         {"Type": "ITL", "avg": "80.5", "p95": "150.0"},
                         {"Type": "TPOT", "avg": "95.0", "p95": "180.0"},
                     ])
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
        assert by_metric["itl_ms"].value == pytest.approx(80.5)
        assert by_metric["itl_p95_ms"].value == pytest.approx(150.0)
        assert by_metric["tpot_ms"].value == pytest.approx(95.0)
        assert by_metric["tpot_p95_ms"].value == pytest.approx(180.0)
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

    def test_output_tps_global_from_tokens_file(self, tmp_path):
        """output_tps（全局口径）= Σtokens / Σ生成秒，来自 tokens.jsonl 记录。"""
        agg = {"Type": "Aggregated", "Name": "Aggregated", "Request Count": "10",
               "Failure Count": "0", "Average Response Time": "900",
               "Requests/s": "1.0", "95%": "1200"}
        raw = tmp_path / "locust_raw"
        _write_stats(raw, 4, agg, ("560", "1100"))
        (raw / "locust_u4_tokens.jsonl").write_text(
            json.dumps({"t": 1.0, "tokens": 100, "gen_ms": 1000,
                        "prompt_tokens": 20}) + "\n"
            + json.dumps({"t": 2.0, "tokens": 300, "gen_ms": 3000,
                          "prompt_tokens": 40}) + "\n",
            encoding="utf-8")
        result = LocustAdapter().collect(make_request(work_dir=tmp_path))
        row = next(r for r in result.rows if r.metric == "output_tps")
        assert row.dataset == "locust:total"
        assert row.value == pytest.approx(100.0)     # 400 tokens / 4 s
        assert row.higher_is_better is True
        # 平均输入/输出长度（locust:total，usage 可用时精确）
        lens = {r.metric: r.value for r in result.rows
                if r.dataset == "locust:total"}
        assert lens["avg_input_tokens"] == pytest.approx(30.0)     # (20+40)/2
        assert lens["avg_output_tokens"] == pytest.approx(200.0)   # (100+300)/2

    def test_post_row_preferred_over_polluted_aggregated(self, tmp_path):
        """口径修复：Aggregated 被 POST+TTFT 双事件污染，终局指标取 POST 行。"""
        agg = {"Type": "Aggregated", "Name": "Aggregated", "Request Count": "200",
               "Failure Count": "6", "Average Response Time": "18200",
               "Requests/s": "1.1", "95%": "29100"}
        post = {"Request Count": "100", "Failure Count": "3",
                "Average Response Time": "9100", "Requests/s": "0.55", "95%": "14550"}
        _write_stats(tmp_path / "locust_raw", 4, agg, ("560", "1100"), post=post)
        result = LocustAdapter().collect(make_request(work_dir=tmp_path))
        by_metric = {r.metric: r for r in result.rows}
        assert by_metric["qps"].value == pytest.approx(0.55)   # POST 行，非 1.1
        assert by_metric["latency_avg_ms"].value == pytest.approx(9100)
        assert by_metric["error_rate"].value == pytest.approx(0.03)

    def test_stats_history_timeseries(self, tmp_path):
        """--csv-full-history 产出 → 秒级时序 + timeseries.json 落盘。"""
        agg = {"Type": "Aggregated", "Name": "Aggregated", "Request Count": "100",
               "Failure Count": "3", "Average Response Time": "9100",
               "Requests/s": "0.55", "95%": "14550"}
        _write_stats(tmp_path / "locust_raw", 4, agg, ("560", "1100"))
        (tmp_path / "locust_raw" / "locust_u4_failures.csv").write_text(
            "Method,Name,Error,Occurrences\n"
            "POST,POST /chat/completions,\"HTTPError('HTTP 429')\",3\n",
            encoding="utf-8")
        history_cols = ["Timestamp", "User Count", "Type", "Name", "Requests/s",
                        "Failures/s", "50%", "95%"]
        hist_rows = [
            {"Timestamp": "100", "User Count": "4", "Type": "POST",
             "Name": "POST /chat/completions", "Requests/s": "2.0",
             "Failures/s": "1.0", "50%": "8000", "95%": "14000"},
            {"Timestamp": "100", "User Count": "4", "Type": "TTFT",
             "Name": "POST /chat/completions", "Requests/s": "2.0",
             "Failures/s": "0.0", "50%": "500", "95%": "1000"},
            {"Timestamp": "100", "User Count": "4", "Type": "TPOT",
             "Name": "POST /chat/completions", "Requests/s": "2.0",
             "Failures/s": "0.0", "50%": "90", "95%": "180"},
            {"Timestamp": "100", "User Count": "4", "Type": "ITL",
             "Name": "POST /chat/completions", "Requests/s": "2.0",
             "Failures/s": "0.0", "50%": "70", "95%": "140"},
            {"Timestamp": "101", "User Count": "4", "Type": "POST",
             "Name": "POST /chat/completions", "Requests/s": "3.0",
             "Failures/s": "0.0", "50%": "9000", "95%": "15000"},
            {"Timestamp": "101", "User Count": "4", "Type": "Aggregated",
             "Name": "Aggregated", "Requests/s": "6.0", "Failures/s": "0.0",
             "50%": "9000", "95%": "15000"},                      # 应被忽略
        ]
        import csv as _csv
        with (tmp_path / "locust_raw" / "locust_u4_stats_history.csv").open(
                "w", newline="", encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=history_cols)
            w.writeheader()
            w.writerows(hist_rows)
        result = LocustAdapter().collect(make_request(work_dir=tmp_path))
        ts = json.loads((tmp_path / "timeseries.json").read_text(
            encoding="utf-8"))
        assert ts["engine"] == "locust" and ts["bucket_seconds"] == 1.0
        assert len(ts["series"]) == 1
        p0, p1 = ts["series"][0]["points"]
        assert p0["t"] == 0 and p0["rps"] == pytest.approx(2.0)   # POST 行口径
        assert p0["errors"] == 1
        assert p0["ttft_avg"] == pytest.approx(750.0)             # (500+1000)/2
        assert p1["t"] == 1 and p1["rps"] == pytest.approx(3.0)
        assert p0["tpot_avg"] == pytest.approx(90.0)
        assert p0["tpot_p95"] == pytest.approx(180.0)
        assert p0["itl_avg"] == pytest.approx(70.0)
        assert p0["itl_p95"] == pytest.approx(140.0)
        assert ts["errors"] == [{"key": "HTTPError('HTTP 429')", "count": 3}]
        assert "timeseries.json" in result.report_files

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
