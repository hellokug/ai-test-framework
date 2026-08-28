"""Suite config loading / validation tests."""

import pytest
from pydantic import ValidationError

from aite.core.loader import load_suite

GOOD = """
run_name: unit-suite
target:
  type: openai_compatible
  base_url: http://localhost:8000/v1
  model: test-model
datasets:
  - {type: local, path: ./x.jsonl}
metrics:
  - {exact_match: {threshold: 0.8}}
  - rouge_l
"""


class TestLoadSuite:
    def test_loads_from_yaml_text_path(self, tmp_path):
        p = tmp_path / "suite.yaml"
        p.write_text(GOOD, encoding="utf-8")
        suite = load_suite(p)
        assert suite.run_name == "unit-suite"
        assert suite.metrics[0].name == "exact_match"
        assert suite.metrics[0].threshold == 0.8
        assert suite.metrics[1].name == "rouge_l"
        assert suite.metrics[1].threshold is None

    def test_metric_normalization_string_entry(self):
        suite = load_suite({"run_name": "s", "target": {"type": "openai_compatible"},
                            "datasets": [{"type": "local", "path": "a.jsonl"}],
                            "metrics": ["exact_match"]})
        assert suite.metrics == [{"name": "exact_match", "threshold": None, "weight": 1.0,
                                  "params": {}}] or suite.metrics[0].name == "exact_match"

    def test_missing_run_name_rejected(self):
        with pytest.raises(ValidationError):
            load_suite({"target": {"type": "openai_compatible"},
                        "datasets": [{"type": "local", "path": "a.jsonl"}],
                        "metrics": ["exact_match"]})

    def test_local_dataset_requires_path(self):
        with pytest.raises(ValidationError):
            load_suite({"run_name": "s", "target": {"type": "openai_compatible"},
                        "datasets": [{"type": "local"}], "metrics": ["exact_match"]})

    def test_builtin_requires_id(self):
        with pytest.raises(ValidationError):
            load_suite({"run_name": "s", "target": {"type": "openai_compatible"},
                        "datasets": [{"type": "builtin"}], "metrics": ["exact_match"]})

    def test_no_metrics_and_no_judges_rejected(self):
        with pytest.raises(ValidationError):
            load_suite({"run_name": "s", "target": {"type": "openai_compatible"},
                        "datasets": [{"type": "local", "path": "a.jsonl"}], "metrics": []})

    def test_bad_pass_strategy(self):
        with pytest.raises(ValidationError):
            load_suite({"run_name": "s", "target": {"type": "openai_compatible"},
                        "datasets": [{"type": "local", "path": "a.jsonl"}],
                        "metrics": ["exact_match"], "pass_strategy": "sometimes"})

    def test_bad_perturbation_name(self):
        with pytest.raises(ValidationError):
            load_suite({"run_name": "s", "target": {"type": "openai_compatible"},
                        "datasets": [{"type": "local", "path": "a.jsonl",
                                      "perturbations": ["bogus"]}],
                        "metrics": ["exact_match"]})

    def test_judge_requires_model(self):
        with pytest.raises(ValidationError):
            load_suite({"run_name": "s", "target": {"type": "openai_compatible"},
                        "datasets": [{"type": "local", "path": "a.jsonl"}],
                        "metrics": [], "judges": [{"type": "score"}]})

    def test_snapshot_is_jsonable(self):
        import json

        suite = load_suite({"run_name": "s", "target": {"type": "openai_compatible",
                                                        "base_url": "http://x/"},
                            "datasets": [{"type": "local", "path": "a.jsonl"}],
                            "metrics": ["exact_match"]})
        json.dumps(suite.snapshot())

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_suite("does/not/exist.yaml")
