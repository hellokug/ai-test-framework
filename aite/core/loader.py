"""Suite configuration (YAML -> pydantic) loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aite import config
from aite.utils.text import PERTURBATION_KINDS

PASS_STRATEGIES = ("all_of", "any_of", "weighted_score")


class RateLimitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rpm: int | None = None
    tpm: int | None = None
    concurrency: int | None = None


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")   # forward vendor-specific fields
    type: str = "openai_compatible"
    name: str | None = None                    # gateway alias (judge/model management)
    base_url: str | None = None
    api_key_env: str | None = None
    api_key: str | None = None
    model: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)   # temperature / max_tokens ...
    headers: dict[str, str] = Field(default_factory=dict)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    timeout_s: float = 120.0
    max_retries: int = 2
    stream: bool = True
    # custom_http connector
    template: dict[str, str] = Field(default_factory=dict)  # url/method/headers/body (jinja2)
    extract: str | None = None                  # dot-path into response JSON

    @property
    def effective_api_key(self) -> str | None:
        import os
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v


class SampleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int | None = None
    seed: int | None = None
    shuffle: bool = False


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "local"
    id: str | None = None                       # builtin benchmark id (mmlu / gsm8k / ...)
    ref: str | None = None                      # 数据集 catalog 引用（§12.4；与 type+path 互斥）
    path: str | None = None
    split: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)   # input/expected/metadata field mapping
    sample: SampleConfig | None = None
    limit: int | None = None
    repeats: int = 1
    perturbations: list[str] = Field(default_factory=list)
    group_by: str | None = None                 # metadata key for per-group summaries
    kwargs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("perturbations")
    @classmethod
    def _check_perturbations(cls, v: list[str]) -> list[str]:
        bad = [p for p in v if p not in PERTURBATION_KINDS]
        if bad:
            raise ValueError(f"unknown perturbations {bad}; available: {list(PERTURBATION_KINDS)}")
        return v

    @property
    def label(self) -> str:
        return self.id or self.path or self.type


class MetricConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    threshold: float | None = None
    weight: float = 1.0
    params: dict[str, Any] = Field(default_factory=dict)


class JudgeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "score"                          # score | pairwise | multi
    name: str | None = None
    model: TargetConfig | None = None
    models: list[TargetConfig] = Field(default_factory=list)   # multi-judge ensemble
    rubric: str | None = None
    threshold: float | None = None
    weight: float = 1.0
    params: dict[str, Any] = Field(default_factory=dict)


class StorageConfig(BaseModel):
    url: str = config.DEFAULT_DB_URL


class SuiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_name: str
    description: str = ""
    target: TargetConfig
    datasets: list[DatasetConfig] = Field(default_factory=list)
    metrics: list[MetricConfig] = Field(default_factory=list)
    judges: list[JudgeConfig] = Field(default_factory=list)
    pass_strategy: str = "all_of"
    weights: dict[str, float] = Field(default_factory=dict)   # metric -> weight (weighted_score)
    report: list[str] = Field(default_factory=lambda: ["rich", "json", "html"])
    output_dir: str = config.DEFAULT_REPORTS_DIR
    storage: StorageConfig = Field(default_factory=StorageConfig)
    plugins: list[str] = Field(default_factory=list)
    concurrency: int | None = None               # CLI/override of target rate_limit.concurrency
    max_cost_usd: float | None = None            # global cost circuit breaker
    case_timeout_s: float | None = None

    @field_validator("pass_strategy")
    @classmethod
    def _check_strategy(cls, v: str) -> str:
        if v not in PASS_STRATEGIES:
            raise ValueError(f"pass_strategy must be one of {PASS_STRATEGIES}")
        return v

    @model_validator(mode="after")
    def _sanity(self) -> SuiteConfig:
        if not self.run_name:
            raise ValueError("run_name is required")
        if not self.datasets:
            raise ValueError("at least one dataset is required")
        if not self.metrics and not self.judges:
            raise ValueError("at least one metric or judge is required")
        for d in self.datasets:
            if d.type == "local" and not d.path:
                raise ValueError("local dataset requires 'path'")
            if d.type == "builtin" and not d.id:
                raise ValueError("builtin dataset requires 'id'")
        for j in self.judges:
            if j.type != "multi" and j.model is None:
                raise ValueError(f"judge of type '{j.type}' requires 'model'")
            if j.type == "multi" and not j.models:
                raise ValueError("multi judge requires 'models' list")
        return self

    def snapshot(self) -> dict[str, Any]:
        """JSON-serializable config snapshot for the DB."""
        return self.model_dump(mode="json", exclude={"storage"})


def _expand_auto_metrics(raw_datasets: Any) -> list[dict]:
    """``metrics: auto`` —— 按数据集推荐指标自动填充（带推荐阈值）。

    仅 builtin 数据集有推荐；local/hf_hub 无推荐。取并集去重后转为
    MetricConfig 原始格式。全部无推荐时报错，避免"auto 空转"。
    """
    from aite.datasets.builtin import recommendations

    ids = [d.get("id") for d in raw_datasets or []
           if isinstance(d, dict) and d.get("type") == "builtin" and d.get("id")]
    recs = recommendations(ids)
    if not recs:
        raise ValueError(
            "metrics: auto found no recommended metrics (only builtin datasets have "
            "recommendations); specify metrics explicitly")
    return [{"name": r["name"], "threshold": r.get("threshold"),
             **({"params": r["params"]} if r.get("params") else {})}
            for r in recs]


def _normalize_metrics(raw: Any) -> list[dict]:
    """``[{exact_match: {threshold: 0.8, extract: number}}, "rouge_l"]``
    -> ``[{"name": ..., "threshold": ..., "params": {...}}]``.

    ``threshold``/``weight`` are top-level MetricConfig fields; every other key
    is routed into ``params`` (passed to the metric constructor).
    """
    out: list[dict] = []
    if not raw:
        return out
    if isinstance(raw, str):
        # 防御：`metrics: auto` 这类裸字符串会被逐字符迭代成 'a','u','t','o' 四个"指标"
        raise ValueError(
            f"metrics must be a list, got bare string '{raw}' "
            f"(write as [{raw}: {{threshold: ...}}] or [{raw}])")
    for item in raw:
        if isinstance(item, str):
            out.append({"name": item})
        elif isinstance(item, dict) and len(item) == 1:
            name, params = next(iter(item.items()))
            entry: dict[str, Any] = {"name": name}
            if isinstance(params, dict):
                for key, value in params.items():
                    if key in ("threshold", "weight"):
                        entry[key] = value
                    else:
                        entry.setdefault("params", {})[key] = value
            elif params is True:
                pass
            else:
                raise ValueError(f"metric '{name}' params must be a mapping")
            out.append(entry)
        else:
            raise ValueError(f"invalid metric entry: {item!r}")
    return out


def load_suite(source: str | Path | dict[str, Any]) -> SuiteConfig:
    """Load + validate a suite from a YAML file path or an already-parsed dict.

    Relative ``datasets[].path`` entries are resolved against the suite file's
    directory (not the process CWD) so suites are location-independent.
    """
    base_dir: Path | None = None
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"suite config not found: {path}")
        base_dir = path.resolve().parent
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        data = dict(source)
    if not isinstance(data, dict):
        raise ValueError("suite config must be a mapping")
    data.setdefault("run_name", data.get("run_name") or "")
    if "metrics" in data:
        raw = data["metrics"]
        if raw == "auto" or raw == ["auto"]:
            data["metrics"] = _expand_auto_metrics(data.get("datasets"))
        else:
            data["metrics"] = _normalize_metrics(raw)
    if base_dir:
        for dataset in data.get("datasets") or []:
            dpath = dataset.get("path") if isinstance(dataset, dict) else None
            if isinstance(dpath, str) and dpath.strip() and not Path(dpath).is_absolute():
                candidate = (base_dir / dpath).resolve()
                if candidate.exists() or not Path(dpath).exists():
                    dataset["path"] = str(candidate)
    return SuiteConfig.model_validate(data)
