"""Compatibility adapters for evalscope / lm-evaluation-harness (no forks).

Layer 1 (here): task-definition conversion - upstream task YAML -> aite suite.
Layer 2: subprocess bridge + result import (`aite bench import`).
Layer 3: expose an OpenAI-compatible reverse proxy (see docs/compat.md).
"""

from aite.datasets.compat.evalscope_adapter import convert_evalscope_task
from aite.datasets.compat.lm_eval_adapter import convert_lm_eval_task

__all__ = ["convert_lm_eval_task", "convert_evalscope_task"]
