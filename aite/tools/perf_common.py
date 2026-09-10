"""perf / locust 两压测适配器的共享解析与常量（DRY 收口，CLAUDE.md 原则 3）。

load：dataset / prompt / synthetic 三选一；mode：baseline / ramp / soak（probe 为
evalscope-perf 特有，由 allow_probe 控制）；duration：整场总时长与 level_duration
每档秒数二选一（probe+duration → 看门狗语义仅 perf 使用）。
两适配器共用同一套错误文案与校验口径，yaml 配置键同义。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 3600                 # 整场压测总超时（秒），extra.timeout 可覆盖
DURATION_DRIVEN_NUMBER_CAP = 1_000_000  # 时长驱动安全上限（先到为准，防默认条数提前结束）
MODES = ("baseline", "ramp", "probe", "soak")
# 平台数据集输入键约定（与 aite/datasets/local 一致，自包含避免深 import）
INPUT_KEYS = ("input", "question", "instruction", "prompt", "query", "user")


def positive_ints(value) -> bool:
    return (isinstance(value, list) and bool(value)
            and all(isinstance(v, int) and not isinstance(v, bool) and v >= 1
                    for v in value))


def expand_ramp(ramp: dict) -> list[int]:
    """from/to/step → 并发梯度（to 必含：爬坡终点即最大并发）。"""
    try:
        start, stop, step = int(ramp["from"]), int(ramp["to"]), int(ramp["step"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("perf.ramp 需要 from / to / step 整数") from exc
    if start < 1 or stop < start or step < 1:
        raise ValueError("perf.ramp 需要 1 ≤ from ≤ to 且 step ≥ 1")
    levels = list(range(start, stop + 1, step))
    if levels[-1] != stop:
        levels.append(stop)
    return levels


def resolve_load(cfg: dict) -> tuple[str, str, dict | None]:
    """负载 dataset / prompt / synthetic 三选一 → (dataset, prompt, synthetic)。

    synthetic 归一化为 {count, prompt_length}（int 已校验）。
    """
    dataset = str(cfg.get("dataset") or "").strip()
    prompt = str(cfg.get("prompt") or "").strip()
    synthetic = cfg.get("synthetic")
    chosen = [k for k, v in (("dataset", dataset), ("prompt", prompt),
                             ("synthetic", synthetic)) if v]
    if len(chosen) > 1:
        raise ValueError("perf.dataset / perf.prompt / perf.synthetic 三选一")
    if not chosen:
        raise ValueError("perf.dataset（数据集文件）/ perf.prompt（固定提示词）"
                         " / perf.synthetic（合成负载）三选一")
    if synthetic:
        if (not isinstance(synthetic, dict)
                or not isinstance(synthetic.get("count"), int)
                or isinstance(synthetic.get("count"), bool) or synthetic["count"] < 1
                or not isinstance(synthetic.get("prompt_length"), int)
                or isinstance(synthetic.get("prompt_length"), bool)
                or synthetic["prompt_length"] < 1):
            raise ValueError("perf.synthetic 须为 {count: ≥1, prompt_length: ≥1}"
                             "（条数 / 每条目标字符数）")
        synthetic = {"count": synthetic["count"],
                     "prompt_length": synthetic["prompt_length"]}
    return dataset, prompt, synthetic


def resolve_mode_parallel(cfg: dict, *, allow_probe: bool) -> tuple[str | None, list[int], dict]:
    """mode 分派 → (mode, parallel, probe_cfg)。

    baseline/ramp/soak/直配(v1) 公共解析；probe 仅 perf 支持（allow_probe=False
    时配置即报错）。probe_cfg 为 probe 段原 dict，供 perf 拼装 SLA 参数。
    """
    mode = cfg.get("mode")
    if mode is not None and mode not in MODES:
        raise ValueError(f"perf.mode 须 ∈ {sorted(MODES)}")
    if mode == "probe" and not allow_probe:
        raise ValueError("该引擎不支持 probe 模式（SLA 二分搜索是 evalscope perf"
                         " 专有）——如需摸高请用 engine: evalscope-perf")
    parallel: list[int] | None = None
    probe_cfg: dict = {}
    if mode in ("baseline", "soak"):
        b = cfg.get(mode) or {}
        if not isinstance(b.get("parallel"), int) or isinstance(b.get("parallel"), bool):
            raise ValueError(f"perf.{mode} 需要 parallel（整数）")
        parallel = [b["parallel"]]
    elif mode == "ramp":
        parallel = expand_ramp(cfg.get("ramp") or {})
    elif mode == "probe":
        probe_cfg = cfg.get("probe") or {}
        start = probe_cfg.get("from", 1)
        if not isinstance(start, int) or isinstance(start, bool) or start < 1:
            raise ValueError("perf.probe.from 须为 ≥1 整数（搜索起点）")
        parallel = [start]
    elif mode is None:
        parallel = cfg.get("parallel") or [1]   # v1 兼容：直配梯度
    if mode is not None and cfg.get("parallel"):
        raise ValueError(f"perf.mode={mode} 模式下不要直接配 perf.parallel")
    if not positive_ints(parallel):
        raise ValueError("perf.parallel 须为 ≥1 的整数数组")
    return mode, parallel, probe_cfg


@dataclass
class DurationPlan:
    """时长双键解析结果。per_level 直传 --duration；watchdog 为整场预算（probe）。"""

    per_level: float | None = None
    watchdog: float | None = None

    @property
    def duration_driven(self) -> bool:
        return self.per_level is not None or self.watchdog is not None


def resolve_durations(cfg: dict, *, mode: str | None, n_levels: int,
                      require: bool = False) -> DurationPlan:
    """duration（整场总时长）/ level_duration（每档秒数）二选一。

    单档直传；多档均分（近似预算）；probe 轮数不可预知 → 看门狗（total）。
    require=True（locust：无请求数上限语义）时长必配。
    """
    total = cfg.get("duration")
    per = cfg.get("level_duration")
    if total is not None and per is not None:
        raise ValueError("perf.duration 与 perf.level_duration 二选一"
                         "（整场总时长 / 每档秒数）")
    if total is not None:
        total = float(total)
        if total <= 0:
            raise ValueError("perf.duration 须为正数（秒）")
    if per is not None:
        per = float(per)
        if per <= 0:
            raise ValueError("perf.level_duration 须为正数（秒）")
    if require and total is None and per is None:
        raise ValueError("该引擎需要时长驱动：配 perf.duration（整场总时长）或"
                         " perf.level_duration（每档秒数）——无请求数上限语义")
    plan = DurationPlan()
    if mode == "probe" and total is not None:
        plan.watchdog = total          # 轮数不可预知，无法均分 → 看门狗
    elif per is not None:
        plan.per_level = per
    elif total is not None:
        plan.per_level = total if n_levels == 1 else total / n_levels
    return plan


def export_prompts(dataset: str, dest: Path) -> int:
    """平台数据集文件（jsonl/csv）→ 逐行 prompt 文件（evalscope custom 数据集）。

    返回导出条数。行数 = 压测条数；输入键取 input/question/instruction/
    prompt/query/user 首个命中（与平台 local 数据集约定一致）。
    """
    src = Path(dataset)
    if not src.is_file():
        raise ValueError(f"perf.dataset 文件不存在: {src}")
    records: list[dict] = []
    with open(src, encoding="utf-8") as fh:
        if src.suffix.lower() == ".csv":
            records = [dict(row) for row in csv.DictReader(fh)]
        else:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"{src.name}:{lineno}: 行不是合法 JSON: {exc}") from exc
                if not isinstance(rec, dict):
                    raise ValueError(f"{src.name}:{lineno}: 行须为 JSON 对象")
                records.append(rec)
    prompts: list[str] = []
    for i, rec in enumerate(records, 1):
        for key in INPUT_KEYS:
            value = rec.get(key)
            if isinstance(value, str) and value.strip():
                prompts.append(value.strip())
                break
        else:
            raise ValueError(f"{src.name}: 第 {i} 条记录无输入字段（{list(INPUT_KEYS)}）")
    if not prompts:
        raise ValueError(f"{src.name}: 没有可用的 prompt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(prompts) + "\n", encoding="utf-8")
    return len(prompts)


_SYNTHETIC_SKELETON = (
    "请围绕以下主题撰写一段说明文字，要求内容连贯、信息量充足：{filler}。"
    "主题编号 {i}，请从多个角度展开论述，保持输出长度稳定。"
)


def generate_synthetic_prompts(count: int, prompt_length: int) -> list[str]:
    """确定性合成负载：固定句式骨架 + 填充文本到目标字符数（每次生成完全一致）。

    「框架自动生成固定输入输出」——无数据集时压测可复现（输入一致则输出分布
    一致，TTFT/吞吐曲线可比）。填充用循环数字串避免真实语义导致的缓存命中。
    """
    if count < 1 or prompt_length < 1:
        raise ValueError("perf.synthetic 的 count / prompt_length 须为 ≥1 整数")
    prompts = []
    for i in range(1, count + 1):
        filler_cycle = "0123456789"
        filler = (filler_cycle * (prompt_length // len(filler_cycle) + 1))[:prompt_length]
        prompts.append(_SYNTHETIC_SKELETON.format(i=i, filler=filler)[: prompt_length + 60])
    return prompts
