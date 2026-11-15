"""本地数据集合并展示 / 在线基准匹配 / 枚举回退（dataset_files 展示层逻辑）。

覆盖：GET /datasets 的本机 ⊕ catalog 合并（未登记行合成）、基准全集的本机
可用标记（强/近似匹配）、主环境缺 evalscope 时的 python_exe 回退与缓存。
"""

import json

import pytest

from aite.datasets import settings as ds_settings
from aite.datasets import userstore
from aite.service import dataset_files


@pytest.fixture()
def local_root(tmp_path, monkeypatch):
    """数据集根落 tmp（AITE_DATASETS_DIR 是设置的 env 覆盖，注册表/目录随根走）。"""
    monkeypatch.setenv("AITE_DATASETS_DIR", str(tmp_path))
    monkeypatch.setattr(ds_settings, "settings_path",
                        lambda: tmp_path / "ds_settings.json")
    return tmp_path


def _catalog_row(name: str, path: str = "", type_: str = "accuracy") -> dict:
    return {"id": f"db-{name}", "type": type_, "name": name, "version": "seed42",
            "source": "hf", "schema_version": "1", "stats": {"count": 12},
            "path": path, "notes": ""}


class TestMergeLocalRows:
    def test_registered_row_keeps_catalog_truth(self, local_root):
        rows = dataset_files.merge_local_rows(
            [_catalog_row("finance"), _catalog_row("scen", type_="functional")])
        assert all(r["registered"] is True for r in rows)
        assert [r["name"] for r in rows] == ["finance", "scen"]  # 原类型/排序不乱

    def test_stale_catalog_path_patched_from_registry(self, local_root):
        acc = local_root / "datasets" / "accuracy"
        acc.mkdir(parents=True)
        real = acc / "swebench_lite.jsonl"
        real.write_text('{"q":1}\n', encoding="utf-8")
        userstore.upsert("swebench_lite", {
            "file": "datasets/accuracy/swebench_lite.jsonl", "external": True,
            "upstream": "hf:SWE-bench/SWE-bench_Lite", "n": 1})
        rows = dataset_files.merge_local_rows(
            [_catalog_row("swebench_lite", path="data/datasets/swebench_lite.jsonl")])
        assert rows[0]["registered"] is True
        assert rows[0]["path"].replace("\\", "/").endswith(
            "datasets/accuracy/swebench_lite.jsonl")

    def test_registry_only_entry_becomes_unregistered_row(self, local_root):
        acc = local_root / "datasets" / "accuracy"
        acc.mkdir(parents=True)
        (acc / "ceval_full.jsonl").write_text('{"q":1}\n{"q":2}\n', encoding="utf-8")
        userstore.upsert("ceval_full", {
            "file": "datasets/accuracy/ceval_full.jsonl", "external": True,
            "upstream": "hf:ceval/ceval-exam", "task": "C-Eval 全量", "n": 2})
        rows = dataset_files.merge_local_rows([])
        assert len(rows) == 1
        row = rows[0]
        assert (row["type"], row["name"], row["registered"]) == \
            ("accuracy", "ceval_full", False)
        assert row["source"] == "registry"
        assert row["stats"] == {"count": 2}
        assert row["notes"] == "C-Eval 全量"
        assert row["path"].endswith("ceval_full.jsonl")

    def test_orphan_file_row_counts_lines(self, local_root):
        acc = local_root / "datasets" / "accuracy"
        acc.mkdir(parents=True)
        (acc / "sample_qa.jsonl").write_text('{"a":1}\n{"a":2}\n{"a":3}\n',
                                             encoding="utf-8")
        row = dataset_files.merge_local_rows([])[0]
        assert (row["source"], row["registered"]) == ("local-file", False)
        assert row["stats"] == {"count": 3}
        assert "未登记" in row["notes"]

    def test_registry_entry_shadowed_by_catalog_not_duplicated(self, local_root):
        acc = local_root / "datasets" / "accuracy"
        acc.mkdir(parents=True)
        (acc / "dupe.jsonl").write_text('{"q":1}\n', encoding="utf-8")
        userstore.upsert("dupe", {"file": "datasets/accuracy/dupe.jsonl",
                                  "external": True, "n": 1})
        rows = dataset_files.merge_local_rows([_catalog_row("dupe")])
        assert [r["name"] for r in rows] == ["dupe"]
        assert all(r["registered"] is True for r in rows)


class TestSplitHfId:
    def test_parses_notes_and_upstream_forms(self):
        assert dataset_files._split_hf_id(
            "hf:ceval/ceval-exam (val+dev, 52 subjects)") == "ceval/ceval-exam"
        assert dataset_files._split_hf_id(
            "hf:princeton-nlp/SWE-bench_Verified") == "princeton-nlp/SWE-bench_Verified"
        assert dataset_files._split_hf_id("remote pull: https://x/y.jsonl") == ""
        assert dataset_files._split_hf_id("") == ""


class TestMatchLocalBenchmarks:
    def test_exact_match_via_dataset_id(self):
        items = [{"type": "accuracy", "name": "swebench_verified",
                  "notes": "hf:princeton-nlp/SWE-bench_Verified"}]
        benches = [{"name": "swe_bench_verified", "pretty_name": "SWE-bench Verified",
                    "dataset_id": "princeton-nlp/SWE-bench_Verified"}]
        out = dataset_files.match_local_benchmarks(items, benches)
        assert out[0]["local"] == "swebench_verified"
        assert out[0]["local_like"] is None

    def test_exact_match_via_normalized_name(self):
        items = [{"type": "accuracy", "name": "gsm8k_full", "notes": ""}]
        benches = [{"name": "gsm8k_full", "dataset_id": "other/gsm8k-full"}]
        out = dataset_files.match_local_benchmarks(items, benches)
        assert out[0]["local"] == "gsm8k_full"

    def test_like_match_via_name_prefix(self):
        items = [{"type": "accuracy", "name": "ceval_full",
                  "notes": "hf:ceval/ceval-exam (val)"}]
        benches = [{"name": "ceval", "dataset_id": "evalscope/ceval"}]
        out = dataset_files.match_local_benchmarks(items, benches)
        assert out[0]["local"] is None
        assert out[0]["local_like"] == "ceval_full"

    def test_no_match_and_functional_rows_ignored(self):
        items = [{"type": "functional", "name": "ceval_full", "notes": "hf:a/b"}]
        benches = [{"name": "ceval", "dataset_id": "evalscope/ceval"},
                   {"name": "aime25", "dataset_id": "evalscope/aime25"}]
        out = dataset_files.match_local_benchmarks(items, benches)
        assert all(b["local"] is None and b["local_like"] is None for b in out)


class TestOnlineBenchmarks:
    def setup_method(self):
        dataset_files.reset_online_cache()

    def teardown_method(self):
        dataset_files.reset_online_cache()

    def test_second_call_served_from_cache(self, monkeypatch):
        from aite import tools as aite_tools

        calls = []

        class FakeAdapter:
            def list_tasks(self):
                calls.append(1)
                return [{"name": "gsm8k"}]

        monkeypatch.setattr(aite_tools, "get_tool", lambda name: FakeAdapter())
        first = dataset_files.online_benchmarks("evalscope")
        second = dataset_files.online_benchmarks("evalscope")
        assert calls == [1]
        assert first["cached"] is False and second["cached"] is True
        # 列举即富化能力维度与私有性（无 tags → 空列表；基准恒为开源），缓存命中返回同一份
        assert first["items"] == second["items"] == \
            [{"name": "gsm8k", "capabilities": [], "ownership": "open"}]

    def test_fallback_via_python_exe_when_importerror(self, monkeypatch):
        from aite import tools as aite_tools
        from aite.core import profiles

        class FakeAdapter:
            def list_tasks(self):
                raise ImportError("evalscope not installed")
            def list_tasks_via(self, python_exe):
                assert python_exe == "D:/fake/python.exe"
                return [{"name": "ceval"}]

        monkeypatch.setattr(aite_tools, "get_tool", lambda name: FakeAdapter())
        monkeypatch.setattr(profiles, "load_common", lambda: {
            "tools": {"extra_args": {"python_exe": "D:/fake/python.exe"}}})
        out = dataset_files.online_benchmarks("evalscope")
        assert out["items"] == [{"name": "ceval", "capabilities": [],
                                 "ownership": "open"}]

    def test_raises_when_no_evalscope_and_no_python_exe(self, monkeypatch):
        from aite import tools as aite_tools
        from aite.core import profiles

        class FakeAdapter:
            def list_tasks(self):
                raise ImportError("evalscope not installed")

        monkeypatch.setattr(aite_tools, "get_tool", lambda name: FakeAdapter())
        monkeypatch.setattr(profiles, "load_common", lambda: {})
        with pytest.raises(RuntimeError, match="python_exe"):
            dataset_files.online_benchmarks("evalscope")


class TestResolveDownloadExternalFallback:
    async def test_unregistered_ref_downloads_from_registry_file(self, local_root):
        acc = local_root / "datasets" / "accuracy"
        acc.mkdir(parents=True)
        (acc / "ceval_full.jsonl").write_text('{"q":"巴黎"}\n', encoding="utf-8")
        userstore.upsert("ceval_full", {
            "file": "datasets/accuracy/ceval_full.jsonl", "external": True, "n": 1})

        class FakeRepo:
            async def get_dataset_catalog(self, dataset_id):
                return None
            async def find_dataset_catalog(self, type_, name):
                return None
            async def list_dataset_catalog(self, *, type_=None, limit=200):
                return []

        out = await dataset_files.resolve_download(FakeRepo(), "ceval_full")
        assert out["kind"] == "file"
        assert out["path"].name == "ceval_full.jsonl"

    async def test_stale_catalog_path_falls_back_to_registry_file(self, local_root):
        acc = local_root / "datasets" / "accuracy"
        acc.mkdir(parents=True)
        (acc / "swebench_lite.jsonl").write_text('{"q":1}\n', encoding="utf-8")
        userstore.upsert("swebench_lite", {
            "file": "datasets/accuracy/swebench_lite.jsonl", "external": True, "n": 1})

        class FakeRepo:
            async def get_dataset_catalog(self, dataset_id):
                if dataset_id == "accuracy:swebench_lite":
                    return _catalog_row("swebench_lite",
                                        path="data/datasets/swebench_lite.jsonl")
                return None
            async def find_dataset_catalog(self, type_, name):
                return None
            async def list_dataset_catalog(self, *, type_=None, limit=200):
                return []

        out = await dataset_files.resolve_download(FakeRepo(), "accuracy:swebench_lite")
        assert out["kind"] == "file"
        assert out["path"].name == "swebench_lite.jsonl"

    async def test_orphan_file_downloads_by_name(self, local_root):
        acc = local_root / "datasets" / "accuracy"
        acc.mkdir(parents=True)
        (acc / "sample_qa.jsonl").write_text('{"q":"首都"}\n', encoding="utf-8")

        class FakeRepo:
            async def get_dataset_catalog(self, dataset_id):
                return None
            async def find_dataset_catalog(self, type_, name):
                return None
            async def list_dataset_catalog(self, *, type_=None, limit=200):
                return []

        out = await dataset_files.resolve_download(FakeRepo(), "sample_qa")
        assert out["kind"] == "file"
        assert out["path"].name == "sample_qa.jsonl"


class TestPerfImportRecords:
    """perf 型真实负载导入（csv/jsonl → input-only jsonl + stats + 登记）。"""

    class _Repo:
        def __init__(self):
            self.rows = []

        async def find_dataset_catalog(self, type_, name):
            return None

        async def upsert_dataset_catalog(self, row):
            self.rows.append(row)
            return row

    async def test_flat_csv_output_stats(self, local_root):
        repo = self._Repo()
        out = await dataset_files.upload_dataset(
            repo, dataset_type="perf", name="real-load", filename="load.csv",
            content="question,answer\n请问账单分期的手续费如何计算？,费率按期数不同而不同\n",
            notes="客户 csv")
        assert out["type"] == "perf" and out["source"] == "upload"
        assert out["stats"] == {"count": 1, "avg_chars": 15, "avg_output_chars": 10}
        path = local_root / "datasets" / "perf" / "real-load.jsonl"
        assert json.loads(path.read_text(encoding="utf-8").strip())["input"] \
            == "请问账单分期的手续费如何计算？"
        assert repo.rows[0]["path"].replace("\\", "/").endswith("perf/real-load.jsonl")

    async def test_sharegpt_conversations_join_human_turns(self, local_root):
        rec = {"conversations": [
            {"from": "human", "value": "帮我查一下昨天那笔转账的到账状态。"},
            {"from": "gpt", "value": "已为您查询，该笔交易正在处理中。"},
            {"from": "human", "value": "如果一直没到账该怎么处理？"},
            {"from": "gpt", "value": "可联系对方银行发起退汇查询。"},
        ]}
        await dataset_files.upload_dataset(
            self._Repo(), dataset_type="perf", name="sharegpt-lite",
            filename="sharegpt.jsonl", content=json.dumps(rec, ensure_ascii=False) + "\n")
        path = local_root / "datasets" / "perf" / "sharegpt-lite.jsonl"
        rec_out = json.loads(path.read_text(encoding="utf-8").strip())
        assert rec_out["input"] == \
            "帮我查一下昨天那笔转账的到账状态。\n如果一直没到账该怎么处理？"

    async def test_record_without_input_raises(self, local_root):
        with pytest.raises(ValueError, match="无输入文本"):
            await dataset_files.upload_dataset(
                self._Repo(), dataset_type="perf", name="bad",
                filename="a.jsonl", content='{"foo": 1}\n')
