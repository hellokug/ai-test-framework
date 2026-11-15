"""judge_calibration 纯函数单测：分数解析/一致性指标/别名归一/占位符替换。"""

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "judge_calibration.py"
_spec = importlib.util.spec_from_file_location("judge_calibration", _SCRIPT)
jc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jc)


class TestParseScore:
    def test_standard_line(self):
        assert jc.parse_score("理由：与参考答案一致。\nSCORE: 1") == 1.0

    def test_chinese_colon_and_case(self):
        assert jc.parse_score("reason... score：0") == 0.0

    def test_fractional_score(self):
        assert jc.parse_score("SCORE: 0.5") == 0.5

    def test_last_score_wins(self):
        assert jc.parse_score("SCORE: 1\n再想想 SCORE: 0") == 0.0

    def test_missing_returns_none(self):
        assert jc.parse_score("我觉得答案是对的") is None
        assert jc.parse_score("") is None


class TestKappa:
    def test_perfect_agreement(self):
        assert jc.cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0

    def test_known_example(self):
        # po=0.8, pe=0.56 → κ≈0.5455（手算锚点）
        got = jc.cohen_kappa([1, 0, 1, 1, 0], [1, 1, 1, 1, 0])
        assert abs(got - 0.24 / 0.44) < 1e-9

    def test_all_same_level_guard(self):
        assert jc.cohen_kappa([1, 1], [1, 1]) == 1.0


class TestWilson:
    def test_bounds_contain_point(self):
        lo, hi = jc.wilson_ci(24, 30)
        assert 0.6 < lo < 0.8 < hi < 0.93

    def test_clamped(self):
        lo, hi = jc.wilson_ci(30, 30)
        assert lo > 0 and hi == 1.0


class TestSpearman:
    def test_perfect_monotonic(self):
        assert abs(jc.spearman([1, 2, 3, 4], [2, 4, 6, 8]) - 1.0) < 1e-9

    def test_constant_input_guard(self):
        assert jc.spearman([1, 1, 1], [1, 2, 3]) == 0.0


class TestRowsAndPrompt:
    def test_resolve_field_aliases(self):
        row = {"input": "Q", "prediction": "A", "answer": "G", "human_label": 1}
        assert jc.resolve_field(row, "question") == "Q"
        assert jc.resolve_field(row, "pred") == "A"
        assert jc.resolve_field(row, "gold") == "G"
        assert jc.resolve_field(row, "human") == 1

    def test_load_rows_jsonl(self, tmp_path):
        f = tmp_path / "cal.jsonl"
        f.write_text(json.dumps({"question": "q", "model_output": "p",
                                 "reference_answer": "g", "human_score": 1},
                                ensure_ascii=False), encoding="utf-8")
        rows = jc.load_rows(f)
        assert rows[0]["human"] == 1.0 and rows[0]["pred"] == "p"

    def test_load_rows_missing_field_raises(self, tmp_path):
        f = tmp_path / "bad.jsonl"
        f.write_text(json.dumps({"question": "q"}, ensure_ascii=False), encoding="utf-8")
        try:
            jc.load_rows(f)
        except ValueError as exc:
            assert "缺字段" in str(exc)
        else:
            raise AssertionError("缺字段应报 ValueError")

    def test_build_user_prompt_replaces_placeholders(self):
        row = {"question": "1+1?", "pred": "2", "gold": "2"}
        got = jc.build_user_prompt("Q:{question} P:{pred} G:{gold}", row)
        assert got.startswith("Q:1+1? P:2 G:2")
        assert "SCORE" in got  # 末尾固定输出契约
