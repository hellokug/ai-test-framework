"""add_hf 字段转换 + 外部 builtin 注册表合并（全离线，不触网）。"""

import json

from aite.datasets import userstore
from aite.datasets.add_hf import convert_records, sanitize_name


class TestConvertRecords:
    def test_auto_field_detection(self):
        recs = [{"question": "1+1?", "answer": "2", "scenario": "math"}]
        out = convert_records(recs)
        assert out[0]["question"] == "1+1?"
        assert out[0]["answer"] == "2"
        assert out[0]["scenario"] == "math"      # 未知字段保留
        assert out[0]["id"] == "case-0"          # 无 id 自动编号

    def test_explicit_field_mapping(self):
        recs = [{"q": "capital of France?", "a": "Paris"}]
        out = convert_records(recs, {"input": "q", "expected": "a"})
        assert out[0]["question"] == "capital of France?"
        assert out[0]["answer"] == "Paris"

    def test_choices_int_answer_to_letter(self):
        # cais/mmlu 风格：answer 是索引
        recs = [{"question": "pick", "choices": ["a", "b", "c"], "answer": 2}]
        out = convert_records(recs)
        assert out[0]["answer"] == "C"
        assert out[0]["choices"] == ["a", "b", "c"]  # choices 保留供选项格式化

    def test_context_string_wrapped_to_list(self):
        recs = [{"question": "q", "answer": "a", "context": "some doc"}]
        out = convert_records(recs)
        assert out[0]["retrieval_context"] == ["some doc"]

    def test_records_without_input_skipped(self):
        assert convert_records([{"foo": "bar"}]) == []

    def test_sanitize_name(self):
        assert sanitize_name("openai/GSM8K") == "openai-GSM8K"
        assert sanitize_name("a b:c") == "a-b-c"


class TestExternalRegistry:
    async def test_external_builtin_loadable(self, tmp_path, monkeypatch):
        """注册到 ./data 的外部数据集应被 builtin 加载器识别并加载。"""
        monkeypatch.chdir(tmp_path)  # userstore 路径相对 CWD
        d = tmp_path / "data" / "datasets"
        d.mkdir(parents=True)
        (d / "myds.jsonl").write_text(
            json.dumps({"id": "m1", "question": "Q1", "answer": "A1"}) + "\n",
            encoding="utf-8")
        userstore.upsert("myds", {"file": "datasets/myds.jsonl", "external": True})

        from aite.datasets import builtin_ids
        assert "myds" in builtin_ids()

        from aite.core.loader import DatasetConfig
        from aite.datasets.builtin import BuiltinDataset

        cases = [c async for c in
                 BuiltinDataset().load(DatasetConfig(type="builtin", id="myds"))]
        assert len(cases) == 1
        assert cases[0].input == "Q1"
        assert cases[0].expected == "A1"
        assert cases[0].dataset == "builtin:myds"

    async def test_packaged_builtin_unaffected(self, tmp_path, monkeypatch):
        """无外部注册表时内置数据集照常可用。"""
        monkeypatch.chdir(tmp_path)
        from aite.datasets import builtin_ids

        assert "gsm8k" in builtin_ids()

    def test_external_recommend_feeds_auto_metrics(self, tmp_path, monkeypatch):
        """外部注册表条目可携带 recommend，供 metrics: auto 使用。"""
        monkeypatch.chdir(tmp_path)
        userstore.upsert("myds", {
            "file": "datasets/myds.jsonl", "external": True,
            "recommend": [{"name": "choice_accuracy", "threshold": 0.25}],
        })
        from aite.datasets.builtin import recommendations

        recs = recommendations(["myds"])
        assert recs == [{"name": "choice_accuracy", "threshold": 0.25}]

    def test_corrupt_registry_degrades_to_builtin(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / userstore.REGISTRY_FILENAME).write_text(
            "{not json", encoding="utf-8")
        assert userstore.load_registry() == {}
