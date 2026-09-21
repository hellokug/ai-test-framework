"""locust 适配器单测（build_config / 负载落盘 / jsonl 聚合 / 模板完整性）。"""

import json
from pathlib import Path

import pytest

from aite.tools.base import ToolRunRequest
from aite.tools.locust_adapter import LocustAdapter


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
        assert cfg["stream"] is True
        assert cfg["jitter"] == 0.0                       # 缺省 0：与 evalscope 同形态
        run = cfg["runs"][0]
        assert run["users"] == 4 and run["seconds"] == 60.0
        assert run["spawn_rate"] == 4                       # 缺省瞬时拉满
        assert run["metrics_file"].endswith("locust_u4_metrics.jsonl")

    def test_qps_mapping(self, tmp_path):
        cfg = LocustAdapter().build_config(make_request(
            work_dir=tmp_path,
            perf={"prompt": "p", "parallel": [8], "duration": 60, "qps": 50}))
        assert cfg["qps"] == 50.0

    def test_qps_validation(self):
        with pytest.raises(ValueError, match="qps"):
            LocustAdapter().build_config(make_request(
                perf={"prompt": "p", "parallel": [4], "duration": 60, "qps": 0}))

    def test_rate_gradient_and_duration_split(self):
        cfg = LocustAdapter().build_config(make_request(perf={
            "prompt": "p", "mode": "ramp",
            "ramp": {"from": 1, "to": 9, "step": 4}, "duration": 90}))
        assert [(r["users"], r["seconds"]) for r in cfg["runs"]] == \
            [(1, 30.0), (5, 30.0), (9, 30.0)]               # 总时长均分 3 档

    def test_v1_direct_gradient(self):
        cfg = LocustAdapter().build_config(make_request(
            perf={"prompt": "p", "parallel": [1, 4], "level_duration": 30}))
        assert [(r["users"], r["seconds"]) for r in cfg["runs"]] == \
            [(1, 30.0), (4, 30.0)]

    def test_duplicate_users_disambiguation(self):
        """重复并发档（稳定性复跑）产物路径必须消歧，防 jsonl 合并/csv 覆盖。"""
        cfg = LocustAdapter().build_config(make_request(
            perf={"prompt": "p", "parallel": [4, 4], "level_duration": 30}))
        stems = [Path(r["metrics_file"]).name for r in cfg["runs"]]
        assert stems == ["locust_u4_1_metrics.jsonl", "locust_u4_2_metrics.jsonl"]
        assert cfg["runs"][0]["csv"] != cfg["runs"][1]["csv"]
        assert cfg["runs"][0]["html"] != cfg["runs"][1]["html"]

    def test_arrival_models(self):
        """到达模型校验与归一化：poisson 需 qps；spike/custom 形状校验。"""
        cfg = LocustAdapter().build_config(make_request(
            perf={"prompt": "p", "parallel": [4], "duration": 60, "qps": 20,
                  "arrival": {"mode": "poisson"}}))
        assert cfg["arrival"] == {"mode": "poisson"}
        cfg = LocustAdapter().build_config(make_request(
            perf={"prompt": "p", "parallel": [4], "duration": 60,
                  "arrival": {"mode": "spike", "spike": {
                      "base_qps": 2, "spike_qps": 20, "period": 60,
                      "spike_duration": 10}}}))
        assert cfg["arrival"]["spike"] == {"base_qps": 2.0, "spike_qps": 20.0,
                                           "period": 60.0, "spike_duration": 10.0}
        profile = [[0, 2], [30, 20], [60, 2]]
        cfg = LocustAdapter().build_config(make_request(
            perf={"prompt": "p", "parallel": [4], "duration": 60,
                  "arrival": {"mode": "custom", "profile": profile,
                              "repeat": True}}))
        assert cfg["arrival"] == {"mode": "custom",
                                  "profile": [[0.0, 2.0], [30.0, 20.0],
                                              [60.0, 2.0]], "repeat": True}

    def test_arrival_validation_errors(self):
        cases = [
            ({"prompt": "p", "parallel": [4], "duration": 60,
              "arrival": {"mode": "poisson"}}, "perf.qps"),
            ({"prompt": "p", "parallel": [4], "duration": 60, "qps": 5,
              "arrival": {"mode": "spike", "spike": {
                  "base_qps": 2, "spike_qps": 20, "period": 10,
                  "spike_duration": 30}}}, "spike_duration"),
            ({"prompt": "p", "parallel": [4], "duration": 60,
              "arrival": {"mode": "custom", "profile": [[30, 5], [0, 2]]}},
             "升序"),
            ({"prompt": "p", "parallel": [4], "duration": 60,
              "arrival": {"mode": "ramp"}}, "poisson | spike | custom"),
            ({"prompt": "p", "parallel": [4], "duration": 60,
              "arrival": "poisson"}, "须为对象"),
            ({"prompt": "p", "parallel": [4], "duration": 60, "jitter": -1},
             "jitter"),
            ({"prompt": "p", "parallel": [4], "duration": 60,
              "extra_body": "x"}, "extra_body"),
        ]
        for perf, err in cases:
            with pytest.raises(ValueError, match=err):
                LocustAdapter().build_config(make_request(perf=perf))

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

    def test_list_tasks_shape(self):
        tasks = LocustAdapter().list_tasks()
        assert tasks[0]["name"] == "chat"
        assert "ttft_ms" in tasks[0]["metrics"]
        assert {"tpot_ms", "itl_ms", "output_tps",
                "avg_input_tokens"} <= set(tasks[0]["metrics"])


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

    def test_synthetic_mode(self, tmp_path):
        cfg = LocustAdapter().build_config(make_request(
            work_dir=tmp_path,
            perf={"synthetic": {"count": 3, "prompt_length": 100},
                  "parallel": [2], "duration": 10}))
        LocustAdapter()._write_load(cfg)
        lines = (tmp_path / "load" / "prompts.txt").read_text(
            encoding="utf-8").splitlines()
        assert len(lines) == 3

    def test_write_load_creates_missing_workdir(self, tmp_path):
        """回归：work_dir 多级不存在时写入不炸（真实 run 首次创建目录）。"""
        cfg = LocustAdapter().build_config(make_request(
            work_dir=tmp_path / "deep" / "nested" / "run-dir"))
        LocustAdapter()._write_load(cfg)
        assert (tmp_path / "deep" / "nested" / "run-dir" / "locustfile.py").is_file()
        assert (tmp_path / "deep" / "nested" / "run-dir" / "load" / "prompts.txt").is_file()

    def test_locustfile_reads_env(self, tmp_path):
        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path))
        LocustAdapter()._write_load(cfg)
        text = (tmp_path / "locustfile.py").read_text(encoding="utf-8")
        for key in ("AITE_LOCUST_URL", "AITE_LOCUST_MODEL", "AITE_LOCUST_STREAM",
                    "AITE_LOCUST_PROMPTS_FILE", "AITE_LOCUST_METRICS_FILE",
                    "AITE_LOCUST_QPS"):
            assert key in text


class TestTemplate:
    def test_structure_chat_in_class(self, tmp_path):
        """ChatUser 单一定义且方法在类体内（防类体断裂回归：曾实跑 0 请求）。"""
        import ast

        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path))
        LocustAdapter()._write_load(cfg)
        text = (tmp_path / "locustfile.py").read_text(encoding="utf-8")
        tree = ast.parse(text)
        classes = [n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "ChatUser"]
        assert len(classes) == 1
        methods = {n.name for n in classes[0].body
                   if isinstance(n, ast.FunctionDef)}
        assert {"on_start", "chat", "_stream_metrics",
                "_json_metrics"} <= methods
        # chat 内所有 self.X 方法调用必须可解析（防方法掉出类体：
        # 曾因类外定义 self._stream_metrics 实跑 0 请求 + AttributeError）
        chat = next(n for n in classes[0].body
                    if isinstance(n, ast.FunctionDef) and n.name == "chat")
        self_calls = {n.attr for n in ast.walk(chat)
                      if isinstance(n, ast.Attribute)
                      and isinstance(n.value, ast.Name) and n.value.id == "self"}
        undefined = self_calls - methods - {"client"}   # client 为基类属性
        assert not undefined, f"chat 调用了未定义方法: {undefined}"

    def test_stream_metrics_present(self, tmp_path):
        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path))
        LocustAdapter()._write_load(cfg)
        text = (tmp_path / "locustfile.py").read_text(encoding="utf-8")
        for marker in ('"usage"', "_stream_metrics", "_record(",
                       "constant_pacing", 'itls', 'tpot_ms',
                       "_Pacer", "EXTRA_BODY", "JITTER",
                       '"Accept-Encoding": "identity"'):
            assert marker in text
        # CPU 回归守卫：逐字节读开销 ~80×/请求，gevent hub 饱和致 TTFT 尖峰
        # （守卫针对调用形态，模板注释里的"勿改 chunk_size=1"提示不受影响）
        assert "iter_lines(chunk_size=1)" not in text
        # keep-alive 回归：[DONE] 分支不得 break（响应未读完 → 连接被弃用
        # → 每请求重付 TCP+TLS 握手，TTFT 平抬 ~100ms）
        assert 'b"[DONE]":' + "\n" + "                break" not in text


def _write_metrics(path: Path, recs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in recs]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ok_rec(**kw):
    base = {"t": 100.0, "ok": 1, "latency_ms": 900.0, "ttft_ms": 560.0,
            "tpot_ms": 30.0, "itl_avg": 28.0, "itl_p50": 20.0, "itl_p90": 40.0,
            "itl_p95": 45.0, "itl_p99": 48.0, "itl_max": 50.0, "itl_n": 40,
            "tokens": 85.0, "gen_ms": 2500.0, "prompt_tokens": 35.0,
            "cached_tokens": None}
    base.update(kw)
    return base


class TestCollect:
    def _one_level(self, tmp_path, recs, users=4):
        raw = tmp_path / "locust_raw"
        _write_metrics(raw / f"locust_u{users}_metrics.jsonl", recs)

    def test_full_metrics_from_jsonl(self, tmp_path):
        recs = [_ok_rec(t=100.0 + i, tokens=85 + i, latency_ms=900 + i * 10,
                        ttft_ms=560 + i * 5, tpot_ms=30 + i, itl_avg=28 + i,
                        itl_p50=20 + i, itl_p90=40 + i, itl_p95=45 + i,
                        itl_p99=48 + i, itl_max=50 + i,
                        cached_tokens=10 + i) for i in range(5)]
        self._one_level(tmp_path, recs)
        result = LocustAdapter().collect(make_request(
            work_dir=tmp_path, metric_thresholds={"error_rate": 0.01,
                                                  "output_tps": 30}))
        by_metric = {r.metric: r for r in result.rows}
        assert all(r.dataset == "locust:u4" for r in result.rows)
        assert by_metric["latency_avg_ms"].value == pytest.approx(920.0)
        assert by_metric["ttft_ms"].value == pytest.approx(570.0)
        assert by_metric["tpot_ms"].value == pytest.approx(32.0)
        assert by_metric["itl_ms"].value == pytest.approx(30.0)
        assert by_metric["qps"].value == pytest.approx(1.25)     # 5 / 4s
        # 多档位（保守上取整分位：pos = int(q·(n−1)) + 1 封顶 n−1）
        assert by_metric["latency_p50_ms"].value == pytest.approx(930.0)
        assert by_metric["latency_p90_ms"].value == pytest.approx(940.0)
        assert by_metric["latency_p95_ms"].value == pytest.approx(940.0)
        assert by_metric["latency_p99_ms"].value == pytest.approx(940.0)
        assert by_metric["latency_max_ms"].value == pytest.approx(940.0)
        assert by_metric["ttft_p50_ms"].value == pytest.approx(575.0)
        assert by_metric["ttft_max_ms"].value == pytest.approx(580.0)
        assert by_metric["tpot_p90_ms"].value == pytest.approx(34.0)
        assert by_metric["itl_p50_ms"].value == pytest.approx(23.0)
        assert by_metric["itl_p99_ms"].value == pytest.approx(52.0)
        assert by_metric["itl_max_ms"].value == pytest.approx(54.0)
        # 缓存命中率 = Σcached / Σprompt = 60 / 175（token 加权）
        assert by_metric["cache_hit_rate"].value == pytest.approx(60 / 175, abs=1e-4)
        # output_tps 与 evalscope 同口径：Σtokens ÷ 墙钟时长
        # （start=100-0.9=99.1, end=104 → wall=4.9s; Σtokens=435）
        assert by_metric["output_tps"].value == pytest.approx(88.8, abs=0.1)
        assert by_metric["error_rate"].value == 0.0
        assert by_metric["error_rate"].passed is True
        assert "timeseries.json" in result.report_files
        # summaries：大屏统计表数据源（overall 行与 CLI 汇总同源）
        ts = json.loads((tmp_path / "timeseries.json").read_text(encoding="utf-8"))
        overall = {s["metric"]: s for s in ts["summaries"] if s["dataset"] is None}
        assert overall["qps"]["value"] == pytest.approx(1.25)
        assert overall["qps"]["count"] == 5
        assert {"locust:u4"} <= {s["dataset"] for s in ts["summaries"]}

    def test_cache_metric_absent_when_unreported(self, tmp_path):
        """端点不回传 cached_tokens → 不产出 cache_hit_rate（未知 ≠ 0）。"""
        self._one_level(tmp_path, [_ok_rec(t=10.0 + i) for i in range(3)])
        result = LocustAdapter().collect(make_request(work_dir=tmp_path))
        names = {r.metric for r in result.rows}
        assert "cache_hit_rate" not in names

    def test_legacy_records_without_itl_tiers_tolerated(self, tmp_path):
        """旧版 jsonl（无 itl_p50 等新键）→ 对应档位跳过，既有指标不受影响。"""
        recs = [{"t": 10.0 + i, "ok": 1, "latency_ms": 900.0, "ttft_ms": 560.0,
                 "tpot_ms": 30.0, "itl_avg": 28.0, "itl_p95": 45.0, "itl_n": 40,
                 "tokens": 85.0, "gen_ms": 2500.0, "prompt_tokens": 35.0}
                for i in range(3)]
        self._one_level(tmp_path, recs)
        result = LocustAdapter().collect(make_request(work_dir=tmp_path))
        by_metric = {r.metric: r for r in result.rows}
        assert by_metric["itl_p95_ms"].value == pytest.approx(45.0)
        assert "itl_p50_ms" not in by_metric and "itl_max_ms" not in by_metric

    def test_error_aggregation(self, tmp_path):
        recs = [_ok_rec(t=1.0, tokens=50), {"t": 2.0, "ok": 0, "tokens": 0,
                                            "error": "HTTP 429"},
                {"t": 3.0, "ok": 0, "tokens": 0, "error": "HTTP 429"}]
        self._one_level(tmp_path, recs)
        result = LocustAdapter().collect(make_request(work_dir=tmp_path))
        by_metric = {r.metric: r for r in result.rows}
        assert by_metric["error_rate"].value == pytest.approx(2 / 3)
        ts = json.loads((tmp_path / "timeseries.json").read_text(encoding="utf-8"))
        assert ts["errors"] == [{"key": "HTTP 429", "count": 2}]

    def test_combined_staircase_and_labels(self, tmp_path):
        """combined 全场阶梯：按墙钟排序、t 重定基全场首秒；series 带 label。"""
        raw = tmp_path / "locust_raw"
        _write_metrics(raw / "locust_u2_metrics.jsonl",
                       [_ok_rec(t=10.0 + i) for i in range(3)])
        _write_metrics(raw / "locust_u4_metrics.jsonl",
                       [_ok_rec(t=20.0 + i) for i in range(3)])
        LocustAdapter().collect(make_request(work_dir=tmp_path))
        ts = json.loads((tmp_path / "timeseries.json").read_text(encoding="utf-8"))
        assert [s["label"] for s in ts["series"]] == ["locust:u2", "locust:u4"]
        assert [p["users"] for p in ts["combined"]] == [2, 2, 2, 4, 4, 4]
        assert ts["combined"][0]["t"] == 0.0            # 全场首秒起
        assert ts["combined"][3]["t"] == 10.0           # 第二档接续（非档内归零）
        assert ts["series"][1]["points"][0]["t"] == 0.0  # 档内相对轴不变

    def test_duplicate_users_labels(self, tmp_path):
        """消歧档名 locust_u<users>_<序> → 独立 label，指标行不再混档。"""
        raw = tmp_path / "locust_raw"
        _write_metrics(raw / "locust_u4_1_metrics.jsonl", [_ok_rec(t=10.0)])
        _write_metrics(raw / "locust_u4_2_metrics.jsonl", [_ok_rec(t=20.0)])
        result = LocustAdapter().collect(make_request(work_dir=tmp_path))
        labels = {r.dataset for r in result.rows}
        assert {"locust:u4#1", "locust:u4#2"} <= labels

    def test_buffered_stream_gate(self, tmp_path):
        """网关缓冲端点（TTFT≈延迟）：TPOT/ITL/output_tps 拒绝入库，TTFT 保留。"""
        recs = [_ok_rec(t=100.0 + i, ttft_ms=899.0, latency_ms=900.0,
                        tpot_ms=5.0, itl_avg=4.0, itl_p95=6.0, itl_n=100,
                        gen_ms=1.0, tokens=200.0) for i in range(5)]
        self._one_level(tmp_path, recs)
        result = LocustAdapter().collect(make_request(work_dir=tmp_path))
        by_metric = {r.metric: r for r in result.rows}
        assert {"tpot_ms", "itl_ms", "tpot_p50_ms", "tpot_max_ms",
                "itl_p50_ms", "itl_max_ms"}.isdisjoint(by_metric)
        # output_tps 墙钟聚合口径对缓冲端点仍可测（token 按墙钟到达，只是不流式）
        assert "output_tps" in by_metric
        assert {"ttft_ms", "latency_avg_ms", "qps"} <= set(by_metric)
        ts = json.loads((tmp_path / "timeseries.json").read_text(encoding="utf-8"))
        pt = ts["series"][0]["points"][0]
        assert pt["tpot_avg"] is None and pt["itl_avg"] is None
        assert pt["latency_avg"] is not None   # 端到端时延仍真实可用

    def test_no_output_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="压测产出"):
            LocustAdapter().collect(make_request(work_dir=tmp_path))


class TestExec:
    def test_missing_binary_raises(self, tmp_path, monkeypatch):
        import shutil as sh
        import sys
        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path))
        LocustAdapter()._write_load(cfg)
        monkeypatch.setattr(sh, "which", lambda name: None)
        # 连同解释器同目录回退一并指向空目录（真 venv 里 locust.exe 实际存在）
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
        with pytest.raises(RuntimeError, match="locust"):
            LocustAdapter()._exec(cfg)

    def test_binary_fallback_next_to_interpreter(self, tmp_path, monkeypatch):
        """PATH 找不到时回退解释器同目录（venv Scripts/bin）——未激活 venv 场景。"""
        import shutil as sh
        import sys

        from aite.tools.subproc import which_or_fail

        monkeypatch.setattr(sh, "which", lambda name: None)
        fake = tmp_path / "locust.exe"
        fake.write_text("", encoding="utf-8")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
        assert which_or_fail("locust", "hint") == str(fake)

    def test_env_injects_metrics_file(self, tmp_path, monkeypatch):
        import shutil as sh
        captured = {}

        def fake_subprocess(args, **kw):
            captured["env"] = kw.get("env_extra", {})
            return 0, "", ""

        monkeypatch.setattr(sh, "which", lambda name: "locust-stub")
        monkeypatch.setattr("aite.tools.locust_adapter.run_tool_subprocess",
                            fake_subprocess)
        cfg = LocustAdapter().build_config(make_request(work_dir=tmp_path,
                                                        perf={"prompt": "p",
                                                              "parallel": [4],
                                                              "duration": 10}))
        LocustAdapter()._write_load(cfg)
        LocustAdapter()._exec(cfg)
        assert captured["env"]["AITE_LOCUST_METRICS_FILE"].endswith(
            "locust_u4_metrics.jsonl")
        assert captured["env"]["AITE_LOCUST_STREAM"] == "1"
        assert "AITE_LOCUST_QPS" not in captured["env"]   # 未配不注入

    def test_env_injects_qps(self, tmp_path, monkeypatch):
        """回归：perf.qps 须注入 AITE_LOCUST_QPS（曾静默失效——限速不生效）。"""
        import shutil as sh
        captured = {}

        def fake_subprocess(args, **kw):
            captured["env"] = kw.get("env_extra", {})
            return 0, "", ""

        monkeypatch.setattr(sh, "which", lambda name: "locust-stub")
        monkeypatch.setattr("aite.tools.locust_adapter.run_tool_subprocess",
                            fake_subprocess)
        cfg = LocustAdapter().build_config(make_request(
            work_dir=tmp_path,
            perf={"prompt": "p", "parallel": [4], "duration": 10, "qps": 50}))
        LocustAdapter()._write_load(cfg)
        LocustAdapter()._exec(cfg)
        assert captured["env"]["AITE_LOCUST_QPS"] == "50.0"
        assert captured["env"]["AITE_LOCUST_JITTER"] == "0.0"   # 未配缺省 0

    def test_env_injects_arrival_jitter_extra_body(self, tmp_path, monkeypatch):
        """到达模型/抖动/请求体扩展三键注入（注入链三段断言，防静默失效回归）。"""
        import shutil as sh
        captured = {}

        def fake_subprocess(args, **kw):
            captured["env"] = kw.get("env_extra", {})
            return 0, "", ""

        monkeypatch.setattr(sh, "which", lambda name: "locust-stub")
        monkeypatch.setattr("aite.tools.locust_adapter.run_tool_subprocess",
                            fake_subprocess)
        cfg = LocustAdapter().build_config(make_request(
            work_dir=tmp_path,
            perf={"prompt": "p", "parallel": [4], "duration": 10, "jitter": 0.5,
                  "arrival": {"mode": "spike", "spike": {
                      "base_qps": 2, "spike_qps": 20, "period": 60,
                      "spike_duration": 10}},
                  "extra_body": {"ignore_eos": True, "min_tokens": 256}}))
        LocustAdapter()._write_load(cfg)
        LocustAdapter()._exec(cfg)
        assert captured["env"]["AITE_LOCUST_JITTER"] == "0.5"
        cfg0 = LocustAdapter().build_config(make_request(
            work_dir=tmp_path,
            perf={"prompt": "p", "parallel": [4], "duration": 10, "jitter": 0}))
        assert cfg0["jitter"] == 0.0                            # 显式 0 关闭
        arrival = json.loads(captured["env"]["AITE_LOCUST_ARRIVAL"])
        assert arrival["mode"] == "spike"
        extra = json.loads(captured["env"]["AITE_LOCUST_EXTRA_BODY"])
        assert extra == {"ignore_eos": True, "min_tokens": 256}
