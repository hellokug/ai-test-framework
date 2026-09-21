"""私域数据集生成器测试：确定性 / 记录形状 / 校验 / 注册（全离线，chdir 隔离 ./data）。"""

import json

import pytest

from aite.datasets.gen_domain import (
    DEFAULT_RECOMMEND,
    corpus_size,
    generate_domain_dataset,
    generate_records,
    list_domains,
)


def test_domains_available():
    assert set(list_domains()) >= {"finance", "government", "legal"}
    for domain in list_domains():
        assert corpus_size(domain) >= 12


class TestGenerateRecords:
    def test_deterministic_same_seed(self):
        assert generate_records("finance", 8, seed=42) == \
            generate_records("finance", 8, seed=42)

    def test_different_seed_differs(self):
        assert generate_records("finance", 8, seed=1) != \
            generate_records("finance", 8, seed=2)

    def test_record_shape(self):
        recs = generate_records("legal", 5, seed=7, distractors=2)
        assert len(recs) == 5
        for rec in recs:
            assert rec["question"] and rec["answer"]
            assert len(rec["retrieval_context"]) == 3  # gold + 2 distractors
            # gold 文档在上下文中（context_recall 可测），但不一定排首位
            assert any(rec["gold_doc"] in c for c in rec["retrieval_context"])
            assert rec["domain"] == "legal" and rec["topic"]

    def test_unknown_domain_rejected(self):
        with pytest.raises(ValueError, match="未知领域"):
            generate_records("medicine", 3)

    def test_n_exceeds_pool_rejected(self):
        with pytest.raises(ValueError, match="语料池"):
            generate_records("finance", 999)

    def test_zero_distractors(self):
        recs = generate_records("government", 4, seed=3, distractors=0)
        assert all(len(r["retrieval_context"]) == 1 for r in recs)


class TestGenerateDomainDataset:
    @pytest.mark.asyncio
    async def test_generate_and_register(self, repo, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # userstore 相对 CWD
        row = await generate_domain_dataset(repo, "government", "gov_test",
                                            n=6, seed=42)
        assert row["type"] == "accuracy" and row["version"] == "seed42"
        assert row["stats"] == {"count": 6, "domain": "government", "distractors": 2}

        path = tmp_path / "data" / "datasets" / "gov_test.jsonl"
        records = [json.loads(line) for line
                   in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(records) == 6

        # userstore 注册带 recommend → {type: builtin, id: gov_test} + metrics: auto 可用
        from aite.datasets import builtin_info

        info = builtin_info("gov_test")
        assert info.get("external") is True
        assert info.get("recommend") == DEFAULT_RECOMMEND

    @pytest.mark.asyncio
    async def test_regenerate_same_seed_reproducible(self, repo, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        await generate_domain_dataset(repo, "finance", "fin_test", n=5, seed=42)
        content = (tmp_path / "data" / "datasets" / "fin_test.jsonl").read_text(
            encoding="utf-8")
        await generate_domain_dataset(repo, "finance", "fin_test", n=5, seed=42)
        assert (tmp_path / "data" / "datasets" / "fin_test.jsonl").read_text(
            encoding="utf-8") == content

    @pytest.mark.asyncio
    async def test_builtin_name_collision_rejected(self, repo, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="冲突"):
            await generate_domain_dataset(repo, "finance", "mmlu", n=4)
