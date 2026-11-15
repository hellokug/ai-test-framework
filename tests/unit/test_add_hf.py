"""add_hf 字段转换 + 外部 builtin 注册表合并（全离线，不触网）。"""

import json

import pytest

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

    def test_sharegpt_conversations_preserved_raw(self):
        """ShareGPT 纯对话记录原样保留（不拍平）：语料池/负载加载器直接消费话轮。"""
        rec = {"id": "s1", "conversations": [
            {"from": "human", "value": "请解释同业拆借利率的形成机制。"},
            {"from": "gpt", "value": "由多家报价行每日报出，剔除极值后加权平均。"}]}
        assert convert_records([rec]) == [rec]

    def test_openai_messages_preserved_raw(self):
        rec = {"messages": [{"role": "user", "content": "你好"},
                            {"role": "assistant", "content": "在的"}]}
        assert convert_records([rec]) == [rec]

    def test_mixed_flat_and_conversation_records(self):
        """扁平字段记录照常映射，对话记录保留——同仓混合 schema 不丢。"""
        recs = [{"question": "1+1?", "answer": "2"},
                {"conversations": [{"from": "human", "value": "讲个笑话"}]}]
        out = convert_records(recs)
        assert out[0]["question"] == "1+1?" and out[0]["answer"] == "2"
        assert out[1]["conversations"][0]["value"] == "讲个笑话"

    def test_explicit_mapping_still_applies_to_conversations(self):
        """显式 fields 映射优先于对话原样保留：input=conversations 拍平为 question。"""
        rec = {"conversations": [{"from": "human", "value": "hi"}]}
        out = convert_records([rec], {"input": "conversations"})
        assert out[0]["question"] == [{"from": "human", "value": "hi"}]

    def test_non_turn_list_not_treated_as_conversation(self):
        """list-of-str 不是话轮数组，无扁平键时维持跳过（不误保留垃圾记录）。"""
        assert convert_records([{"conversations": ["hi", "yo"]}]) == []

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


class TestAddHfPipeline:
    def test_conversations_dataset_end_to_end(self, tmp_path, monkeypatch):
        """真实管线（load_dataset→convert→落盘→注册表）：ShareGPT 对话数据集
        落盘为原始话轮记录，perf 语料池加载器直接可消费（回归：convert 曾把
        纯对话记录整条丢弃，HF 导入语料池是空的）。"""
        pytest.importorskip("datasets")
        monkeypatch.chdir(tmp_path)
        rows = [{"id": f"s{i}", "conversations": [
            {"from": "human",
             "value": f"第{i}轮问题：请完整解释同业拆借利率的形成机制，包括报价行"
                      f"如何报价、如何剔除极值以及加权平均的计算过程与市场意义说明。"},
            {"from": "gpt",
             "value": f"第{i}轮回答：同业拆借利率由多家报价行每日报出，先剔除最高"
                      f"最低报价再取加权平均，反映银行间短期资金成本，是浮息资产定价基准。"
                      + ("\u2028行分隔符须被转义" if i == 2 else "")}]}
            for i in range(5)]
        import datasets as hf_datasets

        def fake_load_dataset(hf_id, subset=None, split=None):
            return hf_datasets.Dataset.from_list(rows)

        monkeypatch.setattr(hf_datasets, "load_dataset", fake_load_dataset)
        from aite.datasets.add_hf import add_hf_dataset
        from aite.tools.perf_dataset import load_corpus

        path = add_hf_dataset("Ryoo72/sharegpt", "sharegpt_pool", split="train", n=5)
        raw = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(raw) == 5 and raw[0]["conversations"][0]["value"].startswith("第")
        reg = userstore.load_registry()
        assert reg["sharegpt_pool"]["n"] == 5          # 外部注册表已登记
        pool = load_corpus(path)                        # U+2028 不得切断记录
        assert len(pool) >= 2 and "同业拆借" in "\n".join(pool)
