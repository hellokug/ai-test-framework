"""数据集统一目录 REST 集成测试（docs/domain-backends.md §12.3 D3 平台切片）。

perf 型登记经 POST /perf/datasets（场景生成）或 /datasets/upload（真实负载
导入）产生；功能型摄取走 /datasets/functional/import（harness.Case 真实校验），
文件区落 tmp 目录。
"""

import json

import pytest


def _make_corpus(tmp_path) -> str:
    lines = [f"第{i:02d}条业务语料，用于压测负载的确定性生成与长度分桶验证测试。"
             for i in range(20)]
    p = tmp_path / "corpus.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def _case_line(case_id: str = "c1") -> str:
    return json.dumps({"id": case_id, "desc": "最小校验用例",
                       "messages": [{"role": "user", "content": "hi"}],
                       "expect": {"nonempty": True}}, ensure_ascii=False)


class TestDatasetsApi:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from aite.datasets import settings as ds_settings
        from aite.web.app import create_app

        # 数据集文件根 + 设置文件全部落 tmp（AITE_DATASETS_DIR 是设置的 env 覆盖）；
        # HF 相关 env 清掉（owner 机器常设 HF_ENDPOINT，会压过设置文件的解析链路）
        monkeypatch.setenv("AITE_DATASETS_DIR", str(tmp_path))
        for var in ("HF_ENDPOINT", "HF_HOME", "AITE_DATASET_REMOTE_URL"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(ds_settings, "settings_path",
                            lambda: tmp_path / "dataset_settings.json")
        app = create_app(storage_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
        with TestClient(app) as c:
            yield c

    def _seed_perf_scene(self, client, tmp_path, name="scene-a"):
        resp = client.post("/api/v1/perf/datasets", json={
            "corpus": _make_corpus(tmp_path), "count": 6,
            "input_lengths": [80], "seed": 42, "name": name})
        assert resp.status_code == 200
        return resp.json()

    def test_perf_generation_auto_registered_and_listed(self, client, tmp_path):
        self._seed_perf_scene(client, tmp_path, name="scene-a")

        listing = client.get("/api/v1/datasets", params={"type": "perf"}).json()
        assert listing["count"] == 1
        row = listing["items"][0]
        assert row["type"] == "perf" and row["name"] == "scene-a"
        assert row["version"] == "seed42"
        assert row["origin"] == "local"
        assert row["stats"]["buckets"]

        # type 过滤隔离
        assert client.get("/api/v1/datasets",
                          params={"type": "functional"}).json()["count"] == 0

    def test_detail_via_type_name_ref(self, client, tmp_path):
        self._seed_perf_scene(client, tmp_path, name="scene-a")
        detail = client.get("/api/v1/datasets/perf:scene-a")
        assert detail.status_code == 200
        assert detail.json()["name"] == "scene-a"

    def test_delete_keeps_files_semantics(self, client, tmp_path):
        generated = self._seed_perf_scene(client, tmp_path, name="scene-b")
        assert client.delete("/api/v1/datasets/perf:scene-b").json()["deleted"] is True
        assert client.get("/api/v1/datasets/perf:scene-b").status_code == 404
        # 登记删除 ≠ 文件删除（与 CLI rm 同语义）
        from pathlib import Path
        assert Path(generated["path"]).is_file()

    def test_list_unknown_type_422_and_unknown_ref_404(self, client):
        assert client.get("/api/v1/datasets",
                          params={"type": "bad"}).status_code == 422
        assert client.get("/api/v1/datasets/nope").status_code == 404

    def test_functional_import_and_force(self, client):
        resp = client.post("/api/v1/datasets/functional/import", json={
            "name": "mini", "jsonl": _case_line(), "notes": "测试摄取"})
        assert resp.status_code == 201
        entry = resp.json()
        assert entry["type"] == "functional" and entry["name"] == "mini"
        assert entry["stats"]["count"] == 1

        # 同名不带 force → 422；带 force → 覆盖登记
        dup = client.post("/api/v1/datasets/functional/import", json={
            "name": "mini", "jsonl": _case_line()})
        assert dup.status_code == 422
        ok = client.post("/api/v1/datasets/functional/import", json={
            "name": "mini", "jsonl": _case_line("c2"), "force": True})
        assert ok.status_code == 201
        assert ok.json()["stats"]["count"] == 1

        # 摄取后目录可查、详情可达
        assert client.get("/api/v1/datasets",
                          params={"type": "functional"}).json()["count"] == 1
        assert client.get("/api/v1/datasets/functional:mini").status_code == 200

    def test_functional_import_bad_case_422(self, client):
        bad = json.dumps({"id": "c1", "expect": {}})   # 缺 desc/messages
        resp = client.post("/api/v1/datasets/functional/import", json={
            "name": "bad", "jsonl": bad})
        assert resp.status_code == 422
        # 坏数据不留登记
        assert client.get("/api/v1/datasets/functional:bad").status_code == 404

    # ---------------------------------------------- 路径与在线源设置
    def test_settings_get_put_roundtrip(self, client):
        from aite import config

        snap = client.get("/api/v1/datasets/settings").json()
        assert snap["settings"]["hf_endpoint"] == ""
        # fixture 设了 AITE_DATASETS_DIR → 解析值跟随 env 且标记 env 锁定
        assert snap["env_locked"]["datasets_dir"] is True
        assert snap["env_locked"]["hf_endpoint"] is False
        assert snap["resolved"]["hf_endpoint"] == config.DEFAULT_HF_ENDPOINT

        put = client.put("/api/v1/datasets/settings", json={
            "hf_endpoint": "https://hf.example.com",
            "remote_url": "https://obs.example.com/ds/"})
        assert put.status_code == 200
        snap2 = client.get("/api/v1/datasets/settings").json()
        assert snap2["settings"]["hf_endpoint"] == "https://hf.example.com"
        assert snap2["resolved"]["hf_endpoint"] == "https://hf.example.com"
        assert snap2["resolved"]["remote_url"] == "https://obs.example.com/ds/"

        # 未知键 / 非法 scheme 显式 422；空串恢复默认
        assert client.put("/api/v1/datasets/settings",
                          json={"nope": "x"}).status_code == 422
        assert client.put("/api/v1/datasets/settings",
                          json={"hf_endpoint": "ftp://x"}).status_code == 422
        client.put("/api/v1/datasets/settings", json={"hf_endpoint": ""})
        final = client.get("/api/v1/datasets/settings").json()
        assert final["resolved"]["hf_endpoint"] == config.DEFAULT_HF_ENDPOINT

    def test_online_status_probe(self, client, monkeypatch):
        from aite.service import dataset_files

        monkeypatch.setattr(dataset_files, "_probe", lambda url, timeout=4.0:
                            {"ok": True, "status": 200, "latency_ms": 3, "error": ""})
        st = client.get("/api/v1/datasets/online/status").json()
        assert st["hf"]["ok"] is True
        assert st["remote"]["configured"] is False
        assert "hf 不通" in st["hint"]

    # ---------------------------------------------- 上传 / 下载
    def test_accuracy_upload_list_download_force(self, client, tmp_path):
        csv_content = "question,answer\n1+1=?,2\n天空为什么蓝?,瑞利散射\n"
        up = client.post("/api/v1/datasets/upload", json={
            "type": "accuracy", "name": "toy-qa", "filename": "toy.csv",
            "content": csv_content, "notes": "csv 上传自动转 jsonl"})
        assert up.status_code == 201
        row = up.json()
        assert row["source"] == "upload" and row["stats"]["count"] == 2
        assert row["path"].endswith("accuracy/toy-qa.jsonl")
        assert tmp_path.joinpath("datasets", "accuracy", "toy-qa.jsonl").is_file()

        listed = client.get("/api/v1/datasets",
                            params={"type": "accuracy"}).json()
        up_row = next(r for r in listed["items"] if r["name"] == "toy-qa")
        assert up_row["origin"] == "local"

        dup = client.post("/api/v1/datasets/upload", json={
            "type": "accuracy", "name": "toy-qa", "filename": "toy.csv",
            "content": csv_content})
        assert dup.status_code == 422
        ok = client.post("/api/v1/datasets/upload", json={
            "type": "accuracy", "name": "toy-qa", "filename": "toy.csv",
            "content": csv_content, "force": True})
        assert ok.status_code == 201

        dl = client.get("/api/v1/datasets/accuracy:toy-qa/download")
        assert dl.status_code == 200
        assert "toy-qa.jsonl" in dl.headers.get("content-disposition", "")
        assert "question" in dl.content.decode("utf-8")

    def test_perf_upload_real_workload(self, client, tmp_path):
        """perf 真实负载导入：csv 输入+真实输出列 → canonical jsonl + 清单可见。"""
        csv_content = (
            "input,output\n"
            "介绍一下你们风控模型对小额高频交易的拦截策略与处置流程。,命中规则后转人工审核并短信告知客户。\n"
            "帮我把上月对账单里金额最大的三笔交易明细导出来。,已为您筛选上月前三笔大额交易并导出明细。\n")
        up = client.post("/api/v1/datasets/upload", json={
            "type": "perf", "name": "customer-real", "filename": "customer.csv",
            "content": csv_content, "notes": "客户真实输入输出"})
        assert up.status_code == 201
        row = up.json()
        assert row["type"] == "perf" and row["source"] == "upload"
        assert row["stats"]["count"] == 2
        assert row["stats"]["avg_output_chars"] > 0
        assert row["path"].replace("\\", "/").endswith("perf/customer-real.jsonl")
        # canonical 产物只留 input 字段（两引擎 perf.dataset 直接消费）
        f = tmp_path.joinpath("datasets", "perf", "customer-real.jsonl")
        lines = [json.loads(line)
                 for line in f.read_text(encoding="utf-8").strip().splitlines()]
        assert all(set(r) == {"input"} for r in lines)
        # GET /perf/datasets 清单按 stats.json 枚举 → 导入产物同列表可见
        perf_list = client.get("/api/v1/perf/datasets").json()
        assert any(i["file"] == "customer-real.jsonl" for i in perf_list["items"])
        # catalog 可查可下载
        assert client.get("/api/v1/datasets/perf:customer-real").status_code == 200
        dl = client.get("/api/v1/datasets/perf:customer-real/download")
        assert dl.status_code == 200 and b'"input"' in dl.content

        # 无输入字段 → 422 不留登记
        bad = client.post("/api/v1/datasets/upload", json={
            "type": "perf", "name": "bad-perf", "filename": "a.jsonl",
            "content": '{"foo": 1}\n'})
        assert bad.status_code == 422 and "无输入文本" in bad.json()["detail"]
        assert client.get("/api/v1/datasets/perf:bad-perf").status_code == 404

    def test_perf_upload_sharegpt_conversations(self, client):
        """ShareGPT 对话数组：human 话轮拼接为单条 prompt，gpt 话轮不进负载。"""
        rec = {"conversations": [
            {"from": "human", "value": "帮我查一下昨天那笔转账的到账状态。"},
            {"from": "gpt", "value": "已为您查询，该笔交易正在处理中。"},
            {"from": "human", "value": "如果一直没到账该怎么处理？"},
            {"from": "gpt", "value": "可联系对方银行发起退汇查询。"},
        ]}
        content = json.dumps(rec, ensure_ascii=False) + "\n"
        up = client.post("/api/v1/datasets/upload", json={
            "type": "perf", "name": "sharegpt-lite", "filename": "sharegpt.jsonl",
            "content": content})
        assert up.status_code == 201
        assert up.json()["stats"]["count"] == 1
        from pathlib import Path
        rec_out = json.loads(Path(up.json()["path"]).read_text(encoding="utf-8").strip())
        assert rec_out["input"] == \
            "帮我查一下昨天那笔转账的到账状态。\n如果一直没到账该怎么处理？"

    def test_upload_rejections(self, client):
        no_input = client.post("/api/v1/datasets/upload", json={
            "type": "accuracy", "name": "bad", "filename": "a.jsonl",
            "content": '{"foo": 1}\n'})
        assert no_input.status_code == 422 and "输入字段" in no_input.json()["detail"]
        bad_suffix = client.post("/api/v1/datasets/upload", json={
            "type": "accuracy", "name": "bad2", "filename": "a.parquet",
            "content": "x"})
        assert bad_suffix.status_code == 422

    def test_download_404_and_builtin_fallback(self, client):
        assert client.get("/api/v1/datasets/nope/download").status_code == 404
        from aite.datasets import builtin_ids

        dl = client.get(f"/api/v1/datasets/{builtin_ids()[0]}/download")
        assert dl.status_code == 200 and dl.content

    # ---------------------------------------------- 本机合并展示（未登记也可见）
    def _seed_local_unregistered(self, tmp_path):
        from aite.datasets import userstore

        acc = tmp_path / "datasets" / "accuracy"
        acc.mkdir(parents=True, exist_ok=True)
        (acc / "ceval_full.jsonl").write_text('{"q":"1"}\n{"q":"2"}\n', encoding="utf-8")
        (acc / "orphan_kit.jsonl").write_text('{"q":"3"}\n', encoding="utf-8")
        fn = tmp_path / "datasets" / "functional" / "orphan-fn"
        fn.mkdir(parents=True, exist_ok=True)
        (fn / "cases.jsonl").write_text('{"id":"c1"}\n{"id":"c2"}\n', encoding="utf-8")
        perf = tmp_path / "datasets" / "perf"
        perf.mkdir(parents=True, exist_ok=True)
        (perf / "orphan-perf.jsonl").write_text('{"prompt":"p"}\n', encoding="utf-8")
        (perf / "orphan-perf.stats.json").write_text("{}", encoding="utf-8")
        userstore.upsert("ceval_full", {
            "file": "datasets/accuracy/ceval_full.jsonl", "external": True,
            "upstream": "hf:ceval/ceval-exam", "task": "C-Eval 全量", "n": 2})

    def test_list_includes_unregistered_local_datasets(self, client, tmp_path):
        self._seed_local_unregistered(tmp_path)
        listing = client.get("/api/v1/datasets").json()
        assert listing["count"] == len(listing["items"])
        by_name = {r["name"]: r for r in listing["items"]}
        reg = by_name["ceval_full"]
        assert reg["registered"] is False and reg["source"] == "registry"
        assert reg["origin"] == "online"          # upstream=hf: → 在线拉取
        assert reg["stats"]["count"] == 2 and reg["notes"] == "C-Eval 全量"
        orphan = by_name["orphan_kit"]
        assert orphan["registered"] is False and orphan["source"] == "local-file"
        assert orphan["origin"] == "local" and orphan["stats"]["count"] == 1
        # 全类型扫描：functional 目录树与 perf 直文件孤儿同样入列
        fn = by_name["orphan-fn"]
        assert fn["type"] == "functional" and fn["stats"]["count"] == 2
        perf = by_name["orphan-perf"]
        assert perf["type"] == "perf" and perf["stats"]["count"] == 1
        assert "orphan-perf.stats" not in by_name   # .stats.json 不算数据集
        # 类型过滤与合并行一致：按类型计数不再漏孤儿
        assert client.get("/api/v1/datasets",
                          params={"type": "functional"}).json()["count"] == 1
        assert client.get("/api/v1/datasets",
                          params={"type": "perf"}).json()["count"] == 1

    def test_download_unregistered_via_registry_fallback(self, client, tmp_path):
        self._seed_local_unregistered(tmp_path)
        dl = client.get("/api/v1/datasets/ceval_full/download")
        assert dl.status_code == 200
        assert '"q":"1"' in dl.content.decode("utf-8")

    # ---------------------------------------------- 在线数据集（evalscope 基准全集）
    _BENCHES = [
        {"name": "swe_bench_verified", "pretty_name": "SWE-bench Verified",
         "dataset_id": "princeton-nlp/SWE-bench_Verified",
         "metrics": ["mean_acc"], "tags": ["Coding"], "few_shot_num": 0,
         "subsets": ["default"]},
        {"name": "gsm8k", "pretty_name": "GSM8K",
         "dataset_id": "AI-ModelScope/gsm8k",
         "metrics": ["mean_acc"], "tags": ["Math"], "few_shot_num": 0,
         "subsets": ["main"]},
    ]

    def test_online_datasets_marks_local_availability(self, client, tmp_path,
                                                      monkeypatch):
        from aite.service import dataset_files

        monkeypatch.setattr(dataset_files, "online_benchmarks",
                            lambda engine="evalscope": {"engine": engine,
                                                        "items": self._BENCHES,
                                                        "count": 2, "cached": True})
        self._seed_local_unregistered(tmp_path)
        # 再放一个与基准 dataset_id 精确对应的本机文件（经 notes hf id 强匹配）
        from aite.datasets import userstore

        acc = tmp_path / "datasets" / "accuracy"
        (acc / "swebench_verified.jsonl").write_text('{"q":1}\n', encoding="utf-8")
        userstore.upsert("swebench_verified", {
            "file": "datasets/accuracy/swebench_verified.jsonl", "external": True,
            "upstream": "hf:princeton-nlp/SWE-bench_Verified", "n": 1})

        data = client.get("/api/v1/datasets/online/datasets").json()
        assert data["count"] == 2 and data["local_ready"] == 1
        by_name = {m["name"]: m for m in data["items"]}
        assert by_name["swe_bench_verified"]["local"] == "swebench_verified"
        assert by_name["gsm8k"]["local"] is None

    def test_online_datasets_unavailable_502(self, client, monkeypatch):
        from aite.service import dataset_files

        def boom(engine="evalscope"):
            raise RuntimeError("主环境无法导入 evalscope，且未配置 python_exe")

        monkeypatch.setattr(dataset_files, "online_benchmarks", boom)
        resp = client.get("/api/v1/datasets/online/datasets")
        assert resp.status_code == 502 and "python_exe" in resp.json()["detail"]

    # ---------------------------------------------- 在线导入（hf / remote）
    def test_hf_import_registers_catalog(self, client, tmp_path, monkeypatch):
        def fake_add_hf(hf_id, name=None, **kw):
            p = tmp_path / "gsm8k_mini.jsonl"
            p.write_text('{"question": "1+1", "answer": "2"}\n', encoding="utf-8")
            return p

        monkeypatch.setattr("aite.datasets.add_hf.add_hf_dataset", fake_add_hf)
        resp = client.post("/api/v1/datasets/hf/import", json={
            "hf_id": "openai/gsm8k", "name": "gsm8k_mini", "split": "test", "n": 50})
        assert resp.status_code == 201
        row = resp.json()
        assert row["source"] == "hf" and row["name"] == "gsm8k_mini"
        listed = client.get("/api/v1/datasets",
                            params={"type": "accuracy"}).json()
        assert next(r for r in listed["items"]
                    if r["name"] == "gsm8k_mini")["origin"] == "online"
        assert client.get("/api/v1/datasets/accuracy:gsm8k_mini").status_code == 200
        assert client.get(
            "/api/v1/datasets/accuracy:gsm8k_mini/download").status_code == 200
        # 字段映射格式错误 → 422
        bad = client.post("/api/v1/datasets/hf/import", json={
            "hf_id": "x/y", "fields": "inputquestion"})
        assert bad.status_code == 422

    def test_hf_import_as_perf_corpus(self, client, tmp_path, monkeypatch):
        """开源语料池导为 perf 型：ShareGPT 对话数组直作语料池（免字段映射）。"""
        conversations = [
            {"from": "human", "value": "请解释同业拆借利率的形成机制，包括报价行报价、"
             "剔除极值与加权平均的完整过程说明内容。"},
            {"from": "gpt", "value": "同业拆借利率由多家报价行每日报出，剔除最高最低后"
             "取平均得到，反映银行间短期资金成本，是浮息资产定价基准。"},
        ]

        def fake_add_hf(hf_id, name=None, **kw):
            p = tmp_path / "sharegpt_pool.jsonl"
            p.write_text("".join(json.dumps({"conversations": conversations},
                                            ensure_ascii=False) + "\n"
                                 for _ in range(12)), encoding="utf-8")
            return p

        monkeypatch.setattr("aite.datasets.add_hf.add_hf_dataset", fake_add_hf)
        resp = client.post("/api/v1/datasets/hf/import", json={
            "hf_id": "Ryoo72/sharegpt", "name": "sharegpt_pool", "type": "perf",
            "n": 100})
        assert resp.status_code == 201
        row = resp.json()
        assert row["type"] == "perf" and row["source"] == "hf"
        assert client.get("/api/v1/datasets/perf:sharegpt_pool").status_code == 200
        # 落盘的对话数组 jsonl 可直接当生成器语料池（INPUT_KEYS 无扁平字段也行）
        from aite.tools.perf_dataset import load_corpus
        pool = load_corpus(row["path"])
        assert len(pool) >= 2 and "报价行" in "\n".join(pool)

    def test_remote_import_pulls_and_registers(self, client, monkeypatch):
        from aite.service import dataset_files

        # 未配置 remote_url 且无直链 → 422 明示去配置
        missing = client.post("/api/v1/datasets/remote/import", json={
            "type": "accuracy", "name": "x"})
        assert missing.status_code == 422 and "remote_url" in missing.json()["detail"]

        seen = {}

        def fake_get(url, timeout=60):
            seen["url"] = url
            return b'{"question": "2+2=?", "answer": "4"}\n'

        monkeypatch.setattr(dataset_files, "_http_get", fake_get)
        client.put("/api/v1/datasets/settings", json={
            "remote_url": "https://obs.example.com/ds/"})

        resp = client.post("/api/v1/datasets/remote/import", json={
            "type": "accuracy", "name": "obs-qa"})
        assert resp.status_code == 201
        assert seen["url"] == "https://obs.example.com/ds/obs-qa.jsonl"
        row = resp.json()
        assert row["source"] == "remote" and row["stats"]["count"] == 1
        listed = client.get("/api/v1/datasets",
                            params={"type": "accuracy"}).json()
        assert next(r for r in listed["items"]
                    if r["name"] == "obs-qa")["origin"] == "online"

        # 显式 url 优先于设置；网络失败 → 502
        direct = client.post("/api/v1/datasets/remote/import", json={
            "type": "accuracy", "name": "direct", "url": "https://cdn.example.com/x.jsonl"})
        assert direct.status_code == 201
        assert seen["url"] == "https://cdn.example.com/x.jsonl"

        def bad_get(url, timeout=60):
            raise dataset_files.httpx.ConnectError("refused")

        monkeypatch.setattr(dataset_files, "_http_get", bad_get)
        net = client.post("/api/v1/datasets/remote/import", json={
            "type": "accuracy", "name": "net-fail", "url": "https://x.example.com/a.jsonl"})
        assert net.status_code == 502
