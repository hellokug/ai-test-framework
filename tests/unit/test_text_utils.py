"""Unit tests for text utils: normalization, extraction, perturbation."""

from aite.utils.text import (
    bleu,
    exact_match,
    extract_choice_letter,
    extract_number,
    normalize_answer,
    perturb_text,
    rouge_l,
    token_f1,
)


class TestNormalize:
    def test_lowercases_and_strips_articles(self):
        assert normalize_answer("The Quick Brown Fox") == "quick brown fox"

    def test_removes_punctuation(self):
        assert normalize_answer("Hello, World!") == "hello world"

    def test_chinese_punctuation(self):
        assert normalize_answer("你好，世界。") == "你好 世界"


class TestExtractNumber:
    def test_hash_marker(self):
        assert extract_number("blah blah\n#### 72") == 72

    def test_boxed(self):
        assert extract_number(r"so \boxed{3.5} done") == 3.5

    def test_last_number(self):
        assert extract_number("first 10 then 20") == 20

    def test_comma_thousands(self):
        assert extract_number("#### 1,575") == 1575

    def test_none(self):
        assert extract_number("no numbers here") is None


class TestExtractChoice:
    def test_leading_letter(self):
        assert extract_choice_letter("B. answer text") == "B"

    def test_answer_prefix(self):
        assert extract_choice_letter("The answer is C because...") == "C"

    def test_boxed_letter(self):
        assert extract_choice_letter(r"\boxed{A}") == "A"


class TestExactMatch:
    def test_plain(self):
        assert exact_match("Paris", "paris")
        assert not exact_match("Paris", "London")

    def test_multiple_refs(self):
        assert exact_match("NYC", ["New York", "NYC"])

    def test_number_mode(self):
        assert exact_match("The answer is #### 42", "42", extract="number")
        assert not exact_match("maybe 41", "42", extract="number")

    def test_number_tolerance(self):
        assert exact_match("#### 3.1416", "3.14", extract="number", tol=0.001)

    def test_choice_mode(self):
        assert exact_match("I'd say B", "B", extract="choice")


class TestNlpStats:
    def test_token_f1_perfect(self):
        assert token_f1("the cat sat", "the cat sat")[2] == 1.0

    def test_token_f1_partial(self):
        _, _, f1 = token_f1("cat dog bird", "cat dog fish")
        assert 0.0 < f1 < 1.0

    def test_rouge_l(self):
        p, r, f = rouge_l("the cat sat on the mat", "the cat sat")
        assert f > 0.5

    def test_bleu_perfect(self):
        assert bleu("the cat sat", ["the cat sat"]) > 0.99

    def test_bleu_zero(self):
        assert bleu("alpha beta", ["gamma delta"]) == 0.0


class TestPerturb:
    def test_typo_changes_text_deterministically(self):
        text = "what is the refund policy please"
        once = perturb_text(text, "typo", seed=1)
        twice = perturb_text(text, "typo", seed=1)
        assert once == twice
        assert once != text

    def test_case(self):
        assert perturb_text("hello", "case", seed=2) != "hello"

    def test_unknown_kind(self):
        import pytest

        with pytest.raises(ValueError):
            perturb_text("x", "nope")
