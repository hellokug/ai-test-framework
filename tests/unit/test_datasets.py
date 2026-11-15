"""Dataset loader tests: local files, builtin, compat adapters."""

import json

import pytest

from aite.core.loader import DatasetConfig
from aite.datasets.builtin import BUILTIN_IDS
from aite.datasets.local import LocalFileDataset


async def collect(loader, config):
    return [c async for c in loader.load(config)]


@pytest.mark.asyncio
async def test_jsonl_defaults_mapping(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps({"id": "x1", "question": "Q1", "answer": "A1", "topic": "t"}) + "\n",
                 encoding="utf-8")
    cases = await collect(LocalFileDataset(), DatasetConfig(type="local", path=str(p)))
    assert len(cases) == 1
    assert cases[0].input == "Q1" and cases[0].expected == "A1"
    assert cases[0].metadata["topic"] == "t"
    assert cases[0].retrieval_context is None


@pytest.mark.asyncio
async def test_jsonl_explicit_fields_and_contexts(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps({"cid": "9", "prompt": "Q", "gold": "G",
                             "docs": ["d1", "d2"]}) + "\n", encoding="utf-8")
    cases = await collect(LocalFileDataset(), DatasetConfig(
        type="local", path=str(p),
        fields={"id": "cid", "input": "prompt", "expected": "gold",
                "retrieval_context": "docs"}))
    assert cases[0].id == "9"
    assert cases[0].retrieval_context == ["d1", "d2"]


@pytest.mark.asyncio
async def test_csv_type_coercion(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("question,answer,score\nQ1,A1,5\nQ2,A2,7\n", encoding="utf-8")
    cases = await collect(LocalFileDataset(), DatasetConfig(type="local", path=str(p)))
    assert len(cases) == 2
    assert cases[0].metadata["score"] == 5


@pytest.mark.asyncio
async def test_json_object_with_items(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"items": [{"question": "Q", "answer": "A"}]}), encoding="utf-8")
    cases = await collect(LocalFileDataset(), DatasetConfig(type="local", path=str(p)))
    assert cases[0].input == "Q"


@pytest.mark.asyncio
async def test_sampling_seed_stable(tmp_path):
    p = tmp_path / "d.jsonl"
    lines = [json.dumps({"id": f"c{i}", "question": f"q{i}", "answer": "a"}) for i in range(20)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cfg_a = DatasetConfig(type="local", path=str(p), sample={"n": 5, "seed": 42})
    cfg_b = DatasetConfig(type="local", path=str(p), sample={"n": 5, "seed": 42})
    a = await collect(LocalFileDataset(), cfg_a)
    b = await collect(LocalFileDataset(), cfg_b)
    assert [c.id for c in a] == [c.id for c in b]
    assert len(a) == 5


@pytest.mark.asyncio
async def test_invalid_jsonl_reports_line(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"id": "1"}\nnot-json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        await collect(LocalFileDataset(), DatasetConfig(type="local", path=str(p)))


@pytest.mark.asyncio
async def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        await collect(LocalFileDataset(), DatasetConfig(type="local", path="missing.jsonl"))


@pytest.mark.asyncio
async def test_builtin_datasets_load():
    from aite.datasets.builtin import BuiltinDataset

    loader = BuiltinDataset()
    for ds_id in BUILTIN_IDS:
        cases = await collect(loader, DatasetConfig(
            type="builtin", id=ds_id, sample={"n": 3, "seed": 1}))
        assert len(cases) == 3, ds_id
        assert all(c.input for c in cases), ds_id
    jailbreak = await collect(loader, DatasetConfig(type="builtin", id="jailbreak"))
    assert all(c.metadata.get("attack") for c in jailbreak)
    hotpot = await collect(loader, DatasetConfig(type="builtin", id="hotpotqa"))
    assert all(c.retrieval_context for c in hotpot)


@pytest.mark.asyncio
async def test_unknown_builtin_rejected():
    from aite.datasets.builtin import BuiltinDataset

    with pytest.raises(ValueError, match="unknown builtin"):
        await collect(BuiltinDataset(), DatasetConfig(type="builtin", id="nope"))


class TestCompatAdapters:
    def test_lm_eval_simple_task(self, tmp_path):
        from aite.datasets.compat.lm_eval_adapter import convert_lm_eval_task

        task = tmp_path / "task.yaml"
        task.write_text(
            "task: dummy\n"
            "dataset_path: cais/mmlu\n"
            "dataset_name: abstract_algebra\n"
            "test_split: test\n"
            "doc_to_text: \"{{question}}\"\n"
            "doc_to_choice: \"{{choices}}\"\n"
            "doc_to_target: \"{{answer}}\"\n"
            "metric_list:\n  - metric: acc\n", encoding="utf-8")
        fragment = convert_lm_eval_task(task, threshold=0.7)
        assert fragment["datasets"][0]["id"] == "cais/mmlu"
        assert fragment["datasets"][0]["fields"] == {"input": "question", "expected": "answer"}
        assert fragment["metrics"][0]["choice_accuracy"]["threshold"] == 0.7

    def test_lm_eval_unmapped_metric_warns(self, tmp_path):
        from aite.datasets.compat.lm_eval_adapter import convert_lm_eval_task

        task = tmp_path / "t.yaml"
        task.write_text(
            "task: x\ndataset_path: d\nmetric_list:\n  - metric: weird_metric\n",
            encoding="utf-8")
        fragment = convert_lm_eval_task(task)
        assert any("weird_metric" in w for w in fragment["details"]["warnings"])

    def test_evalscope_task(self, tmp_path):
        from aite.datasets.compat.evalscope_adapter import convert_evalscope_task

        task = tmp_path / "evalscope.yaml"
        task.write_text(
            "name: ceval\n"
            "dataset:\n"
            "  args:\n"
            "    path: ceval/ceval-exam\n"
            "    name: account\n"
            "    split: val\n"
            "scoring:\n  acc:\n    numeric: 0\n", encoding="utf-8")
        fragment = convert_evalscope_task(task)
        assert fragment["datasets"][0]["id"] == "ceval/ceval-exam"
        assert fragment["datasets"][0]["kwargs"]["name"] == "account"
        assert fragment["metrics"][0]["choice_accuracy"] == {}


def test_online_benchmarks_capabilities_enriched(monkeypatch):
    """基准全集列举时富化能力维度（evalscope tags → 平台画像，缓存一次）。"""
    from aite.service import dataset_files

    class _FakeAdapter:
        @staticmethod
        def list_tasks():
            return [{"name": "gsm8k", "tags": ["Math", "MCQ"]},
                    {"name": "weird", "tags": ["Chinese"]}]

    monkeypatch.setattr("aite.tools.get_tool", lambda engine: _FakeAdapter())
    dataset_files.reset_online_cache()
    try:
        out = dataset_files.online_benchmarks("evalscope")
    finally:
        dataset_files.reset_online_cache()
    caps = {i["name"]: i["capabilities"] for i in out["items"]}
    assert caps["gsm8k"] == ["math"] and caps["weird"] == []
    assert all(i["ownership"] == "open" for i in out["items"])   # 基准恒为开源
