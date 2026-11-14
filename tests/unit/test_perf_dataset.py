"""性能场景数据集生成器单测（模块 J：确定性 / 长度分桶 / 前缀共享 / 校验）。"""

import json
from pathlib import Path

import pytest

from aite.tools.perf_dataset import generate_perf_dataset, load_corpus


def _make_corpus(tmp_path: Path) -> Path:
    """语料池：20 行 × ~50 字符 → ~1000 字符 → 2-3 个池块。"""
    lines = [f"第{i:02d}条业务语料，用于压测负载的确定性生成与长度分桶验证测试。"
             for i in range(20)]
    p = tmp_path / "corpus.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _gen(corpus: Path, **kw) -> dict:
    return generate_perf_dataset({"corpus": str(corpus), "count": 6,
                                  "input_lengths": [80, 160], **kw})


class TestGenerate:
    def test_deterministic_same_seed(self, tmp_path):
        corpus = _make_corpus(tmp_path)
        a = _gen(corpus, seed=42)
        b = _gen(corpus, seed=42)
        assert [r["input"] for r in a["records"]] == [r["input"] for r in b["records"]]
        c = _gen(corpus, seed=43)
        assert [r["input"] for r in a["records"]] != [r["input"] for r in c["records"]]

    def test_length_buckets_exact(self, tmp_path):
        """长度分桶：actual_chars 与桶目标一致（字符口径）。"""
        corpus = _make_corpus(tmp_path)
        result = _gen(corpus, input_lengths=[100, 200])
        buckets = {r["bucket"] for r in result["records"]}
        assert buckets == {100, 200}
        assert all(r["actual_chars"] == r["bucket"] for r in result["records"])
        assert result["stats"]["buckets"] == {"100": 3, "200": 3}

    def test_weights_distribution(self, tmp_path):
        corpus = _make_corpus(tmp_path)
        result = generate_perf_dataset({
            "corpus": str(corpus), "count": 20, "input_lengths": [100, 300],
            "input_weights": [0.75, 0.25]})
        assert result["stats"]["buckets"] == {"100": 15, "300": 5}

    def test_prefix_sharing(self, tmp_path):
        """共享前缀：同组同桶样本前缀逐字节一致（KV cache 命中的前提）。"""
        corpus = _make_corpus(tmp_path)
        result = _gen(corpus, prefix_sharing=0.5, prefix_groups=2)   # count=6 → 3 共享
        shared = [r for r in result["records"] if r["prefix_group"] is not None]
        assert len(shared) == 3
        assert result["stats"]["shared_ratio"] == 0.5
        same_bucket = {}
        for r in shared:
            same_bucket.setdefault((r["prefix_group"], r["bucket"]), []).append(r)
        for members in same_bucket.values():
            if len(members) >= 2:
                p = members[0]["prefix_chars"]
                assert members[0]["input"][:p] == members[1]["input"][:p]
        unique = [r for r in result["records"] if r["prefix_group"] is None]
        assert all(r["prefix_chars"] == 0 for r in unique)

    def test_output_tokens_meta(self, tmp_path):
        corpus = _make_corpus(tmp_path)
        result = _gen(corpus, output_tokens=256)
        assert all(r["output_tokens"] == 256 for r in result["records"])


class TestWriteAndCorpus:
    def test_dest_dir_writes_file_and_stats(self, tmp_path):
        corpus = _make_corpus(tmp_path)
        out = tmp_path / "out"
        result = generate_perf_dataset({"corpus": str(corpus), "count": 4,
                                        "input_lengths": [100], "name": "scene-a"},
                                       dest_dir=out)
        f = Path(result["path"])
        assert f.is_file() and f.name.startswith("scene-a-")
        assert len(f.read_text(encoding="utf-8").strip().splitlines()) == 4
        assert Path(result["path"]).with_name(
            f.name.replace(".jsonl", ".stats.json")).is_file()
        assert "records" not in result                     # 落盘后不回传全量
        assert all(len(p["input"]) <= 200 for p in result["preview"])

    def test_corpus_jsonl_text_fields(self, tmp_path):
        ds = tmp_path / "corpus.jsonl"
        ds.write_text(
            json.dumps({"text": "\n".join("这是一段足够长的业务语料内容。" * 3
                                          for _ in range(6))})
            + "\n" + json.dumps({"input": "\n".join("另一段足够长的业务语料内容。" * 3
                                                    for _ in range(6))})
            + "\n", encoding="utf-8")
        pool = load_corpus(ds)
        assert len(pool) >= 2

    def test_load_corpus_dir_empty_raises(self, tmp_path):
        (tmp_path / "d").mkdir()
        with pytest.raises(ValueError, match="语料池目录为空"):
            load_corpus(tmp_path / "d")
        with pytest.raises(ValueError, match="不存在"):
            load_corpus(tmp_path / "nope")


class TestValidation:
    def test_validation_errors(self, tmp_path):
        corpus = _make_corpus(tmp_path)
        cases = [
            ({"corpus": str(corpus), "count": 0, "input_lengths": [100]}, "count"),
            ({"corpus": str(corpus), "count": 5, "input_lengths": []}, "input_lengths"),
            ({"corpus": str(corpus), "count": 5, "input_lengths": [100],
              "input_weights": [1.0, 1.0]}, "input_weights"),
            ({"corpus": str(corpus), "count": 5, "input_lengths": [100],
              "prefix_sharing": 1.5}, "prefix_sharing"),
            ({"corpus": str(corpus), "count": 5, "input_lengths": [100],
              "prefix_groups": 0}, "prefix_groups"),
            ({"corpus": str(corpus), "count": 5, "input_lengths": [100],
              "output_tokens": 0}, "output_tokens"),
            ({"corpus": str(corpus), "count": 5, "input_lengths": [100],
              "seed": "x"}, "seed"),
            ({"count": 5, "input_lengths": [100]}, "corpus"),
            ({"corpus": str(tmp_path / "nope.txt"), "count": 5,
              "input_lengths": [100]}, "不存在"),
        ]
        for cfg, err in cases:
            with pytest.raises(ValueError, match=err):
                generate_perf_dataset(cfg)


class TestCorpusFormats:
    """语料池新格式：ShareGPT 对话数组（免拍平入池）+ csv（Excel 另存直用）。"""

    def _sharegpt_rec(self) -> dict:
        return {"conversations": [
            {"from": "human", "value": "请解释同业拆借利率的形成机制，包括报价行报价、"
             "剔除极值与加权平均的完整过程，内容尽量详细以便测试。"},
            {"from": "gpt", "value": "同业拆借利率由多家报价行每日报出，剔除最高最低后取"
             "平均得到，反映银行间短期资金成本，是浮息资产定价的重要基准。"},
            {"from": "human", "value": "那 Shibor 与 LPR 之间是什么关系？请从期限结构、"
             "报价机制与传导路径三个角度分别展开说明。"},
            {"from": "gpt", "value": "Shibor 反映银行间批发资金利率，LPR 在政策利率基础上"
             "加点形成，两者通过银行负债成本传导联动，但期限结构并不相同。"},
        ]}

    def test_sharegpt_conversations_corpus(self, tmp_path):
        ds = tmp_path / "corpus.jsonl"
        ds.write_text("".join(json.dumps(self._sharegpt_rec(), ensure_ascii=False)
                              + "\n" for _ in range(4)), encoding="utf-8")
        pool = load_corpus(ds)
        assert len(pool) >= 2
        joined = "\n".join(pool)
        assert "报价行" in joined and "LPR" in joined     # human + gpt 话轮都入池

    def test_messages_role_content_corpus(self, tmp_path):
        """OpenAI 风格 messages/role/content 同样识别。"""
        rec = {"messages": [
            {"role": "user", "content": "帮忙起草一份数据中心机房巡检操作说明，覆盖配电、"
             "温湿度与消防三部分，每部分列出检查项与合格标准。"},
            {"role": "assistant", "content": "机房巡检操作说明：一、配电系统检查 UPS 输入"
             "输出状态、电池温度与负载率并记录读数；二、环境检查精密空调运行状态与温湿度。"},
        ]}
        ds = tmp_path / "corpus.jsonl"
        ds.write_text("".join(json.dumps(rec, ensure_ascii=False) + "\n"
                              for _ in range(8)), encoding="utf-8")
        assert "巡检" in "\n".join(load_corpus(ds))

    def test_csv_corpus_input_column(self, tmp_path):
        rows = ["id,input,output"]
        for i in range(12):
            rows.append(f"{i},这是第{i}条客服会话记录，客户咨询了账单分期手续费的详细"
                        f"计算方式、适用期数与当月优惠活动说明,客服已按标准话术解答")
        ds = tmp_path / "corpus.csv"
        ds.write_text("\n".join(rows), encoding="utf-8")
        pool = load_corpus(ds)
        assert len(pool) >= 2
        assert "账单分期" in "\n".join(pool)

    def test_record_without_text_raises(self, tmp_path):
        ds = tmp_path / "corpus.jsonl"
        ds.write_text('{"id": 1, "meta": {"a": "b"}}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="无文本字段"):
            load_corpus(ds)
